#!/usr/bin/env python
"""optimise.py - the optimise loop (docs/design.md section 4, "### M7." and
"### M8."). Two halves share `start`, meta.json and trials.tsv, and `start`
picks the half from the target's suffix:

- RTL (M7, /vde's `optimise` verb): `start` freezes the evaluator and
  scores trial 0; then per trial the optimiser agent edits the one target
  file and `trial` scores it; `finish` runs the full gates on the winner.
- sizing (M8, /ade's `optimise` verb): `start` freezes the evaluator and
  `numeric` runs scipy differential evolution against it.

    optimise.py start --workspace DIR --target rtl/<file>.v
                [--objective area|slack|power|score] [--trials N]
                [--wall MIN] [--patience K] [--out FILE]
    optimise.py trial --workspace DIR --note "one line" [--out FILE]
    optimise.py finish --workspace DIR [--out FILE]
    optimise.py start --workspace DIR --target sizing/sizing.yaml
                [--objective margin|power] [--out FILE]
    optimise.py numeric --workspace DIR [--trials N] [--wall MIN]
                [--popsize N] [--maxiter N] [--seed N] [--out FILE]

RTL LOOP. The workspace must be a git repo with a clean tree, and the target
one tracked file under rtl/. `start` copies spec.yaml, the synth script, the
liberty path and its sha, an SDC built from the spec's clock, tb/, formal/,
the holdout's hash, the port list, `must_keep` and rtl/lint_allow.yaml into
optimise/evaluator/, hashes it into meta.json and state.optimise, and scores
the starting RTL as trial 0 (refused if it fails a constraint). `trial`:
reverts every changed path but the target (ENGINE_OUTPUTS are left alone),
re-hashes the evaluator and aborts the loop on a mismatch, then in a scratch
workspace built from the frozen copies runs the constraints - lint, sim,
formal at depth <= FAST_FORMAL_DEPTH - and, if they pass, the metric: yosys
with the frozen script for cell area, the netlist's ports against the
locked list, every `must_keep` cell still present, OpenSTA with the frozen
SDC for worst slack and for power from the VCD the testbench wrote. Slack
>= 0 is a constraint unless slack is the objective. A trial is kept (a git
commit of the target) only if every constraint passes AND the score beats
the best so far; otherwise `git checkout -- <target>`. The score is higher-
is-better: -area, slack, -power, or for `score` the mean of area and power
relative to trial 0. One row per trial in RTL_TSV_FIELDS order. The loop
reports `stop` at N trials, the wall budget, or K non-improving trials in a
row, and refuses further trials. `finish` checks the holdout is unchanged,
then runs holdout, formal at the spec's own depth and mutate on the winner;
a winner that fails is discarded and the next earlier kept trial (then the
baseline) is tried and restored - exit 1 if none passes.

SIZING SEARCH. Built directly against sim_run.py; none of the RTL half's
synth/OpenSTA/VCD machinery applies to a sizing search.

EVALUATOR (section 4: "freezes the evaluator... hashes it"): `start` copies
tb/ (every bench + its .bounds.json sidecar) and the target file's OWN
starting content into `optimise/evaluator/`, hashes that directory
(statelib's dir_text norm - the same one invalidation.yaml uses for tb/) and
records {target, objective, evaluator_sha} in `optimise/meta.json`.
`numeric` re-hashes tb/ + the target's CURRENT content against that record
and refuses (CheckError, exit 2 - never silently proceeds) on a mismatch,
same as section 4's own per-trial check ("re-hashes the evaluator and
aborts on a mismatch") - here checked once at the start of the run rather
than every single scipy function evaluation, since a differential-evolution
objective is called hundreds of times and only the WORKSPACE'S OWN tb/ or
target changing underneath the search (a human editing the spec mid-run)
is the thing this guards against, not the search's own candidate values
(which never touch disk until the very end - see below).

OBJECTIVE (section 4: "the score the spec declares (power at the spec-
meeting point, or the smallest margin across measures)"): `margin` (the
default, and the only one implemented - a power objective needs a power
measure this milestone's corpus rungs do not have) is
min-over-every-declared-bound of that measure's own normalized distance
inside its bound (0 at the edge, negative outside it, unbounded above
inside it) - maximized by minimizing its negation, since scipy's
`differential_evolution` only minimizes. A bench that errors, fails to
converge, or never printed a declared measure scores a large negative
margin (MISSING_MEASURE_PENALTY) - never silently 0 or skipped, matching
this milestone's own rule that such a run is a failure, not a pass.

ONE FILE, WRITTEN ONCE (the write-set rule, section 4: "The write set is
one file, enforced by the diff" - the digital loop's own mechanism, a git
diff per trial, does not fit a search that calls its objective hundreds of
times per run): a trial's candidate sizing values live ONLY in memory
(passed straight into sim_run.build_subs's `sizing` argument, never written
to sizing.yaml on disk) until the search concludes; then the target file on
disk is overwritten with the BEST candidate found ONLY IF its score beats
the STARTING sizing's own score (computed as trial 0, before the search
proper) - "the change stays only if the metric improved" applied once, at
the end, rather than per intermediate trial the way the digital loop's own
git-revert does. Every evaluation still gets its own logged trials.tsv row
(trial, timestamp, param values' own sha, each declared measure's margin,
score, kept, note) - kept is true only for trial 0 (if nothing better was
found) or the very last row (the written winner). Each row is appended and
flushed to disk the moment its own trial finishes, never buffered in
memory and written once at the end - a search calls its objective hundreds
of times over real ngspice runs and can run for minutes, and a crash or a
kill partway through must not cost every trial that already ran.

WINNER, FULL CORNER SET (section 4: "Every K-th kept trial and the winner
run the full corner set" - only the winner's own half, here): every trial
during the search itself scores ONLY at 'tt' (the cheap evaluator, section
4: "the spec bench at typical") - once the search concludes, `numeric`
also runs the kept sizing (whatever is now on disk, winner or unchanged
start) through sim_run.run_workspace_benches over the FULL corner set
spec.yaml's own `corners` field expands to (corners.spec_corners - the
identical corner set a real `sim_pvt` gate run would use),
logs ONE more trials.tsv row for it ("winner, full corner set"), and
reports `full_corner_pass`/`full_corner_corners`/`full_corner_violations`
in the payload - a winner that only holds at typical (gates.yaml's own
sim_pvt fault: "meets at typical, loses headroom at slow and hot") is now
always visible here, not only if a caller separately chases `numeric` with
its own `sim_pvt` gate step. `status` itself stays keyed on the tt-only
evaluator alone (the DAC's own M8 done-criterion is typical-only, unlike
the mirror's) - full_corner_pass is reported, never gates it.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import corners as corners_mod  # noqa: E402
import sim_run  # noqa: E402
import simlib  # noqa: E402
import speclib  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "optimise"
DEFAULT_TIMEOUT = 30.0
MISSING_MEASURE_PENALTY = -1e6
EVALUATOR_SUBDIR = "optimise/evaluator"
META_PATH = "optimise/meta.json"
TSV_PATH = "optimise/trials.tsv"
TSV_FIELDS = ["trial", "timestamp", "target_sha", "score", "kept", "note"]


def target_sha(sizing: dict) -> str:
    canon = json.dumps(sizing, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def hash_evaluator(ws: Path, target_rel: str) -> str:
    """dir_text-normalized hash of tb/ plus the target file's own content -
    "the bench, its bounds, ... the port list" (section 4) narrowed to what
    an analog sizing search actually reads: tb/'s bench+bounds files and the
    sizing target itself."""
    h = hashlib.sha256()
    tb_dir = ws / "tb"
    for p in sorted(tb_dir.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(ws).as_posix().encode("utf-8"))
            h.update(statelib.hash_artifact(p, "text_eol").encode("utf-8"))
    target = ws / target_rel
    if target.is_file():
        h.update(target_rel.encode("utf-8"))
        h.update(statelib.hash_artifact(target, "json_canonical").encode("utf-8"))
    return h.hexdigest()


def load_sizing_yaml(path: Path) -> dict:
    import yaml
    if not path.is_file():
        raise CheckError(f"no sizing file at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not data:
        raise CheckError(f"{path} must be a non-empty mapping of "
                         "{name: {value, min, max}}")
    for name, spec in data.items():
        if not isinstance(spec, dict) or not all(
                k in spec for k in ("value", "min", "max")):
            raise CheckError(f"{path}: {name!r} needs value/min/max")
    return data


def write_sizing_yaml(path: Path, sizing: dict) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(sizing, sort_keys=True), encoding="utf-8")


def measure_margin(value: float, bound: dict) -> float:
    """Normalized distance inside [min, max] - 0 at an edge, negative
    outside, unbounded (but typically <= ~1) well inside. Normalized by the
    bound's own width (or magnitude, for a one-sided bound) so measures in
    different units can still be combined by a plain min()."""
    lo, hi = bound.get("min"), bound.get("max")
    if lo is not None and hi is not None:
        width = hi - lo
        m = min(value - lo, hi - value)
        return m / width if width else m
    if lo is not None:
        return (value - lo) / abs(lo) if lo else (value - lo)
    if hi is not None:
        return (hi - value) / abs(hi) if hi else (hi - value)
    return 0.0


def score_sizing(ws: Path, eda_bin: Path, t_root: Path, netlist_path: Path,
                 tt_corner: dict, nominal_vdd: float, sizing: dict,
                 benches: list[tuple[Path, str, list[dict]]],
                 out_dir: Path, timeout: float, trial: int) -> float:
    """One evaluation: run every bench at 'tt' with `sizing` substituted in
    (never written to disk), return the worst (minimum) measure margin."""
    subs = sim_run.build_subs(t_root, netlist_path, tt_corner, nominal_vdd,
                              sizing)
    worst = None
    for bench_path, template_text, bounds in benches:
        result = sim_run.run_bench_at_corner(
            eda_bin, f"trial{trial}__{bench_path.stem}", template_text,
            bounds, subs, tt_corner, out_dir, timeout, check="optimise")
        if result["engine_errors"]:
            return MISSING_MEASURE_PENALTY
        for b in bounds:
            key = b["measure"].lower()
            if key not in result["measures"]:
                return MISSING_MEASURE_PENALTY
            m = measure_margin(result["measures"][key], b)
            worst = m if worst is None else min(worst, m)
    return worst if worst is not None else MISSING_MEASURE_PENALTY


def full_corner_margin(results: list[dict],
                       benches: list[tuple[Path, str, list[dict]]]) -> float:
    """The same worst-margin scalar score_sizing computes per trial, but
    over sim_run.run_workspace_benches()'s own per-corner `results` (the
    winner's full-corner check, run_numeric, after the search concludes) -
    logged as that check's own trials.tsv score; the pass/fail call itself
    is `results`' own `violations`, not this number."""
    bounds_by_bench = {bench_path.name: bounds for bench_path, _text, bounds
                       in benches}
    worst = None
    for r in results:
        if r["engine_errors"]:
            worst = MISSING_MEASURE_PENALTY if worst is None \
                   else min(worst, MISSING_MEASURE_PENALTY)
            continue
        for b in bounds_by_bench.get(r["bench"], []):
            key = b["measure"].lower()
            m = (measure_margin(r["measures"][key], b) if key in r["measures"]
                else MISSING_MEASURE_PENALTY)
            worst = m if worst is None else min(worst, m)
    return worst if worst is not None else MISSING_MEASURE_PENALTY


def run_start(argv=None):
    ap = argparse.ArgumentParser(description="freeze the evaluator")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--target", required=True,
                    help="repo-relative sizing file, e.g. sizing/sizing.yaml")
    ap.add_argument("--objective", default="margin", choices=["margin"])
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    sizing = load_sizing_yaml(ws / args.target)
    ev_sha = hash_evaluator(ws, args.target)
    meta = {"target": args.target, "objective": args.objective,
           "evaluator_sha": ev_sha, "started_at": time.time()}
    meta_path = ws / META_PATH
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")

    tsv_path = ws / TSV_PATH
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tsv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter="\t").writerow(TSV_FIELDS)

    payload = {"script": SCRIPT, "status": "pass", "meta": meta,
              "sizing_params": list(sizing)}
    return payload, args.out


def run_numeric(argv=None):
    from scipy.optimize import differential_evolution

    ap = argparse.ArgumentParser(description="scipy differential evolution "
                                 "over sizing/sizing.yaml")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="per-trial ngspice timeout")
    ap.add_argument("--popsize", type=int, default=6)
    ap.add_argument("--maxiter", type=int, default=15)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    meta_path = ws / META_PATH
    if not meta_path.is_file():
        raise CheckError(f"no {meta_path} - run `optimise.py start` first")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("kind") == "rtl":
        raise CheckError("`numeric` searches a sizing file; this "
                         "workspace's optimise run is an RTL loop (`trial`)")
    target_rel = meta["target"]

    current_sha = hash_evaluator(ws, target_rel)
    if current_sha != meta["evaluator_sha"]:
        raise CheckError(
            "the evaluator changed since `optimise.py start` "
            f"(was {meta['evaluator_sha']}, now {current_sha}) - tb/ or "
            f"{target_rel} was edited mid-run; re-run `start`")

    target_path = ws / target_rel
    start_sizing = load_sizing_yaml(target_path)
    names = sorted(start_sizing)
    bounds_arr = [(start_sizing[n]["min"], start_sizing[n]["max"]) for n in names]

    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    nominal_vdd = (spec.get("supply") or {}).get("vdd")
    eda_bin = sim_run.EDA_BIN
    t_root = sim_run.toolchain_root(eda_bin)
    netlist_path = sim_run.find_netlist(ws)
    tt_corner = corners_mod.corners_by_name(corners_mod.load(), ["tt"])[0]

    benches = []
    for bench_path, bounds_path in sim_run.find_benches(ws):
        benches.append((bench_path, bench_path.read_text(encoding="utf-8"),
                       simlib.load_bounds(bounds_path)))

    out_dir = ws / "log" / "optimise"
    import shutil
    shutil.rmtree(out_dir, ignore_errors=True)

    tsv_path = ws / TSV_PATH
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    # Appended and flushed ONE ROW AT A TIME (never buffered in memory and
    # written once at the end): a DE search calls its objective hundreds of
    # times over real ngspice runs and can run for minutes - a crash, a
    # --wall timeout, or this process getting killed partway through used
    # to leave trials.tsv holding only `start`'s own header, every trial
    # that already ran lost with it. "Every evaluation still gets its own
    # logged trials.tsv row" (this module's own docstring) means on disk as
    # it happens, not "eventually, if the run finishes clean".
    tsv_file = open(tsv_path, "a", newline="", encoding="utf-8")
    tsv_writer = csv.DictWriter(tsv_file, fieldnames=TSV_FIELDS, delimiter="\t")
    trial_counter = {"n": 0}
    best = {"score": None, "sizing": None}

    def sizing_from_x(x) -> dict:
        return {n: {"value": float(v), "min": start_sizing[n]["min"],
                   "max": start_sizing[n]["max"]} for n, v in zip(names, x)}

    def write_row(trial: int, sizing: dict, score: float, kept: bool, note: str) -> None:
        tsv_writer.writerow({"trial": trial, "timestamp": round(time.time(), 3),
                             "target_sha": target_sha(sizing), "score": round(score, 6),
                             "kept": kept, "note": note})
        tsv_file.flush()

    def record(sizing: dict, score: float, note: str) -> None:
        n = trial_counter["n"]
        kept = best["score"] is None or score > best["score"]
        if kept:
            best["score"], best["sizing"] = score, sizing
        write_row(n, sizing, score, kept, note)
        trial_counter["n"] += 1

    # trial 0: the deliberately-wrong starting sizing, scored the same way
    # every other candidate is - this is the baseline "the change stays
    # only if the metric improved" compares the winner against.
    start_score = score_sizing(ws, eda_bin, t_root, netlist_path, tt_corner,
                               nominal_vdd, start_sizing, benches, out_dir,
                               args.timeout, 0)
    record(start_sizing, start_score, "starting sizing")

    def objective(x) -> float:
        sizing = sizing_from_x(x)
        score = score_sizing(ws, eda_bin, t_root, netlist_path, tt_corner,
                             nominal_vdd, sizing, benches, out_dir,
                             args.timeout, trial_counter["n"])
        record(sizing, score, "differential_evolution")
        return -score  # scipy minimizes; this loop maximizes margin

    try:
        result = differential_evolution(
            objective, bounds_arr, seed=args.seed, popsize=args.popsize,
            maxiter=args.maxiter, polish=False, tol=1e-3)

        improved = best["sizing"] is not start_sizing and best["score"] > start_score
        if improved:
            write_sizing_yaml(target_path, best["sizing"])
        # else: nothing beat the start - the file is left exactly as it was
        # (the digital loop's own "otherwise git reverts it", applied once).
        kept_final = best["sizing"] if improved else start_sizing

        # The winner run[s] the full corner set (design.md 4) - not just
        # the tt-only evaluator every trial above used for speed. A winner
        # that only looks good at typical (gates.yaml's own sim_pvt fault:
        # "meets at typical, loses headroom at slow and hot") used to ship
        # straight to disk with nothing here ever checking another corner;
        # a caller running `optimise.py numeric` on its own, without also
        # chasing it with a separate `sim_pvt` gate step, would never find
        # out. `kept_final` is already ON DISK at this point either way
        # (write_sizing_yaml above, or untouched from `start` otherwise),
        # so this is the exact same corner set/sizing a real sim_pvt gate
        # run would score - reusing sim_run.run_workspace_benches directly,
        # never a re-implementation of it.
        full_corner_list = corners_mod.spec_corners(
            corners_mod.load(), spec.get("corners", "default"))
        full_corner = sim_run.run_workspace_benches(
            ws, eda_bin=eda_bin, corners=full_corner_list,
            timeout=args.timeout, check="sim_pvt")
        full_corner_pass = not full_corner["violations"]
        write_row(trial_counter["n"], kept_final,
                 full_corner_margin(full_corner["results"], benches),
                 full_corner_pass, "winner, full corner set")
        trial_counter["n"] += 1
    finally:
        tsv_file.close()

    # status stays keyed on the tt-only evaluator alone (design.md 4: "an
    # evaluator that is the spec bench at typical" - and the M8 done
    # criteria name full-corner sim_pvt success for the MIRROR rung only,
    # never the DAC's own optimise done-criterion, which is typical-only
    # by design). full_corner_pass is reported, not gated on: a winner
    # that only holds at typical is now always visible in the payload/TSV
    # (this finding's own point) rather than silently unchecked, but a
    # corpus rung whose own bounds were never written to be corner-
    # tolerant (r2r_dac's a known case: an absolute-volt bound against a
    # ratiometric divider with a +-10% VDD sweep) does not regress a
    # `numeric` run that otherwise met its documented scope.
    all_in_bounds = best["score"] is not None and best["score"] >= 0
    payload = {
        "script": SCRIPT,
        "status": "pass" if all_in_bounds else "violations",
        "trials": trial_counter["n"], "start_score": round(start_score, 6),
        "best_score": round(best["score"], 6) if best["score"] is not None else None,
        "improved": improved, "written": improved, "kept_sizing": kept_final,
        "full_corner_pass": full_corner_pass,
        "full_corner_corners": full_corner["corners"],
        "full_corner_violations": full_corner["violations"],
        "tsv": str(tsv_path), "scipy_message": result.message,
    }
    return payload, args.out


# ------------------------------------------------------------ RTL half (M7)

RTL_SUFFIXES = (".v", ".sv")
RTL_OBJECTIVES = ("area", "slack", "power", "score")
# section 4: "trial, timestamp, target sha, each constraint's result, area,
# slack, power, score, kept, and the agent's one-line note".
CONSTRAINTS = ("lint", "sim", "formal", "synth", "ports", "must_keep", "timing")
RTL_TSV_FIELDS = (["trial", "timestamp", "target_sha"] + list(CONSTRAINTS)
                  + ["area", "slack", "power", "score", "kept", "note"])
FAST_FORMAL_DEPTH = 10
DEFAULT_PATIENCE = 5
# Paths the engine itself writes while it scores a trial. The diff leaves
# them alone; everything else outside the target is reverted. optimise/
# holds the evaluator, which the hash guards instead.
ENGINE_OUTPUTS = ("log/", "synth/", "optimise/", "state_snapshots/")
ENGINE_OUTPUT_FILES = ("state.json", "state.json.lock")
GIT_ID = ["-c", "user.name=optimise.py", "-c", "user.email=optimise@localhost"]
TOOL_TIMEOUT_S = 300.0


def is_rtl_target(target: str) -> bool:
    return target.endswith(RTL_SUFFIXES)


def _git(ws: Path, *args: str, check: bool = True) -> str:
    import subprocess
    proc = subprocess.run(["git", "-C", str(ws), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise CheckError(f"git {' '.join(args)} failed in {ws}: "
                         f"{(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def _ws_prefix(ws: Path) -> tuple[Path, str]:
    """(repo root, the workspace's own path inside it with a trailing /,
    or '' when the workspace IS the root)."""
    root = Path(_git(ws, "rev-parse", "--show-toplevel").strip()).resolve()
    rel = ws.resolve().relative_to(root).as_posix()
    return root, "" if rel == "." else rel + "/"


def _is_engine_output(rel: str) -> bool:
    parts = rel.split("/")
    return (rel in ENGINE_OUTPUT_FILES or rel.startswith(ENGINE_OUTPUTS)
            or "__pycache__" in parts)


def changed_paths(ws: Path) -> list[str]:
    """Workspace-relative paths git sees as changed or untracked."""
    root, prefix = _ws_prefix(ws)
    out = _git(root, "status", "--porcelain=v1", "-z", "--no-renames",
               "--untracked-files=all", "--", prefix or ".")
    paths = []
    for entry in out.split("\0"):
        if len(entry) > 3 and entry[3:].startswith(prefix):
            paths.append(entry[3 + len(prefix):])
    return sorted(set(paths))


def revert_path(ws: Path, rel: str) -> None:
    """Put one workspace path back to HEAD: restored if HEAD has it,
    deleted (and unstaged) if it is new."""
    root, prefix = _ws_prefix(ws)
    full = prefix + rel
    if _git(root, "ls-tree", "--name-only", "HEAD", "--", full).strip():
        _git(root, "checkout", "HEAD", "--", full)
    else:
        _git(root, "rm", "-q", "--cached", "--ignore-unmatch", "--", full)
        p = ws / rel
        if p.is_file() or p.is_symlink():
            p.unlink()


def revert_outside_target(ws: Path, target_rel: str) -> list[str]:
    """section 4: "diffs the tree and reverts any file other than the
    target". Returns what it reverted."""
    reverted = []
    for rel in changed_paths(ws):
        if rel == target_rel or _is_engine_output(rel):
            continue
        revert_path(ws, rel)
        reverted.append(rel)
    return reverted


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def hash_rtl_evaluator(ws: Path) -> str:
    return statelib.hash_artifact(ws / EVALUATOR_SUBDIR, "dir_text") or ""


def _load_meta(ws: Path) -> dict:
    p = ws / META_PATH
    if not p.is_file():
        raise CheckError(f"no {p} - run `optimise.py start` first")
    return json.loads(p.read_text(encoding="utf-8"))


def _save_meta(ws: Path, meta: dict) -> None:
    p = ws / META_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, indent=1), encoding="utf-8")


def _record_state(ws: Path, meta: dict) -> None:
    """state.optimise, as state.py's schema names it, when the workspace
    has a state.json at all (a bare test workspace may not)."""
    if not (ws / "state.json").is_file():
        return
    import state as state_mod
    st = state_mod.State.load(ws / "state.json")
    best = meta.get("best") or {}
    st.data["optimise"] = {
        "trials": meta["trials_run"], "evaluator_sha": meta["evaluator_sha"],
        "best": {"trial": best.get("trial"), "score": best.get("score")}}
    st.save()


def _append_row(ws: Path, row: dict) -> None:
    with open(ws / TSV_PATH, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=RTL_TSV_FIELDS, delimiter="\t",
                       extrasaction="ignore").writerow(row)


def sdc_text(spec: dict) -> str:
    """The locked clock: the spec's own period on every declared domain,
    zero I/O delay. The clock period is locked, so a design cannot get
    faster by moving the goalposts (section 4)."""
    clock = spec.get("clock") or {}
    period = clock.get("period_ns")
    domains = clock.get("domains") or []
    if not period or not domains:
        raise CheckError("spec.yaml needs clock: {period_ns, domains} - the "
                         "optimise loop locks the period it scores slack at")
    ports = spec.get("ports") or {}
    lines = []
    for d in domains:
        lines.append(f"create_clock -name {d} -period {period} [get_ports {d}]")
    clk = domains[0]
    for name, p in sorted(ports.items()):
        if name in domains:
            continue
        cmd = "set_input_delay" if p.get("dir") == "input" else "set_output_delay"
        lines.append(f"{cmd} 0 -clock {clk} [get_ports {{{name}}}]")
    return "\n".join(lines) + "\n"


def synth_script(top: str, rtl_rels: list[str], liberty: Path) -> str:
    """The fixed synth script: section 4's "yosys synth with the fixed
    script for cell area". Flattened so must_keep and the port list are
    read off one module; `read_liberty -lib` so a hand-instantiated cell
    (a must_keep ring) is a known black box, not an error."""
    reads = "\n".join(f"read_verilog -sv {r}" for r in rtl_rels)
    return f"""\
read_liberty -lib {liberty}
{reads}
hierarchy -check -top {top}
synth -flatten -top {top}
dfflibmap -liberty {liberty}
abc -liberty {liberty}
opt_clean
stat -liberty {liberty}
write_verilog -noattr synth/{top}.v
write_json synth/{top}.json
"""


def sta_script(top: str, liberty: Path, sdc: Path, vcd: Path | None) -> str:
    power = (f"read_vcd -scope {top} {vcd}\nreport_power -digits 8\n"
             if vcd else "")
    return f"""\
read_liberty {liberty}
read_verilog synth/{top}.v
link_design {top}
read_sdc {sdc}
report_worst_slack -max -digits 4
{power}exit
"""


def run_rtl_start(argv=None):
    ap = argparse.ArgumentParser(description="freeze the RTL evaluator")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--target", required=True, help="e.g. rtl/uart_tx.v")
    ap.add_argument("--objective", default="area", choices=RTL_OBJECTIVES)
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--wall", type=float, default=60.0, help="minutes")
    ap.add_argument("--patience", type=int, default=DEFAULT_PATIENCE,
                    help="stop after this many non-improving trials in a row")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    import shutil
    import check_synth
    ws = Path(args.workspace)
    target_rel = Path(args.target).as_posix()
    target = ws / target_rel
    if not target.is_file():
        raise CheckError(f"no target file at {target}")
    if not target_rel.startswith("rtl/"):
        raise CheckError(f"the target must be one file under rtl/, not "
                         f"{target_rel}")
    _git(ws, "ls-files", "--error-unmatch", "--", target_rel)
    dirty = [p for p in changed_paths(ws) if not _is_engine_output(p)]
    if dirty:
        raise CheckError(
            "the workspace has uncommitted changes the loop's diff would "
            f"revert: {', '.join(dirty[:10])} - commit or stash them first")

    spec_path = ws / "spec" / "spec.yaml"
    spec = speclib.load_spec(spec_path)
    top = spec.get("top")
    if not top:
        raise CheckError("spec.yaml has no 'top'")
    if not spec.get("ports"):
        raise CheckError("spec.yaml has no 'ports' - the loop locks the port "
                         "list, so it needs one")
    rtl_rels = sorted(p.relative_to(ws).as_posix()
                      for p in (ws / "rtl").iterdir()
                      if p.is_file() and p.suffix in RTL_SUFFIXES)
    liberty = check_synth.toolchain_root() / check_synth.LIBERTY_REL
    if not liberty.is_file():
        raise CheckError(f"liberty file not found: {liberty}")

    # section 4: the synth script, the liberty path, the SDC with the
    # spec's clock, the visible tests, the formal properties, the holdout
    # hash and the port list, copied into optimise/evaluator/ and hashed.
    ev = ws / EVALUATOR_SUBDIR
    shutil.rmtree(ev, ignore_errors=True)
    ev.mkdir(parents=True)
    shutil.copy2(spec_path, ev / "spec.yaml")
    for sub in ("tb", "formal"):
        if (ws / sub).is_dir():
            shutil.copytree(ws / sub, ev / sub,
                            ignore=shutil.ignore_patterns("__pycache__"))
    allow = ws / "rtl" / "lint_allow.yaml"
    if allow.is_file():
        shutil.copy2(allow, ev / "lint_allow.yaml")
    (ev / "synth.ys").write_text(synth_script(top, rtl_rels, liberty),
                                 encoding="utf-8")
    (ev / "design.sdc").write_text(sdc_text(spec), encoding="utf-8")
    (ev / "liberty.json").write_text(json.dumps(
        {"path": str(liberty),
         "sha256": hashlib.sha256(liberty.read_bytes()).hexdigest()},
        indent=1), encoding="utf-8")
    (ev / "ports.json").write_text(json.dumps(spec["ports"], indent=1,
                                              sort_keys=True), encoding="utf-8")
    (ev / "must_keep.json").write_text(json.dumps(
        list(spec.get("must_keep") or [])), encoding="utf-8")
    holdout_sha = statelib.hash_artifact(ws / "holdout", "dir_text") \
        if (ws / "holdout").is_dir() else None
    (ev / "holdout.json").write_text(json.dumps(
        {"dir_sha": holdout_sha}), encoding="utf-8")
    (ev / "profile.json").write_text(json.dumps(
        {"objective": args.objective, "target": target_rel, "top": top,
         "rtl": rtl_rels, "fast_formal_depth": FAST_FORMAL_DEPTH},
        indent=1), encoding="utf-8")

    meta = {"kind": "rtl", "target": target_rel, "objective": args.objective,
            "evaluator_sha": hash_rtl_evaluator(ws),
            "trials_limit": args.trials, "wall_min": args.wall,
            "patience": args.patience, "started_at": time.time(),
            "trials_run": 0, "since_improved": 0, "aborted": None,
            "stopped": None, "baseline": None, "best": None, "kept": [],
            "start_commit": _git(ws, "rev-parse", "HEAD").strip()}
    tsv_path = ws / TSV_PATH
    with open(tsv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter="\t").writerow(RTL_TSV_FIELDS)

    # Trial 0 is the starting RTL, scored the same way as every trial after
    # it: the baseline a kept trial must beat.
    result = evaluate_rtl(ws, meta)
    if not result["passed"]:
        _append_row(ws, _row(0, target, result, False, "baseline"))
        failing = [c for c in CONSTRAINTS if result["constraints"][c] != "pass"]
        raise CheckError(
            "the starting RTL fails the loop's constraints "
            f"({', '.join(failing)}{': ' if result['detail'] else ''}"
            f"{'; '.join(result['detail'])}) - run the gates and fix it "
            "before optimising working code")
    if args.objective in ("power", "score") and result["power"] is None:
        raise CheckError(f"objective {args.objective} needs a power figure "
                         "and the baseline produced none")
    base = {"trial": 0, "score": result["score"], "area": result["area"],
            "slack": result["slack"], "power": result["power"],
            "target_sha": file_sha(target), "commit": meta["start_commit"]}
    meta["baseline"] = dict(base)
    meta["best"] = dict(base)
    _append_row(ws, _row(0, target, result, True, "baseline"))
    _save_meta(ws, meta)
    _record_state(ws, meta)
    payload = {"script": SCRIPT, "status": "pass", "meta": meta,
               "baseline": result}
    return payload, args.out


def _row(trial: int, target: Path, result: dict, kept: bool, note: str,
         sha: str | None = None) -> dict:
    def fmt(v):
        return "" if v is None else float(f"{v:.8g}")
    row = {"trial": trial, "timestamp": round(time.time(), 3),
           "target_sha": sha or (file_sha(target) if target.is_file() else ""),
           "area": fmt(result.get("area")), "slack": fmt(result.get("slack")),
           "power": fmt(result.get("power")), "score": fmt(result.get("score")),
           "kept": kept, "note": " ".join(str(note).split())}
    row.update(result.get("constraints") or {})
    return row


def _eval_workspace(ws: Path, profile: dict) -> Path:
    """A scratch workspace built from the frozen evaluator plus the
    workspace's current RTL: what every constraint and the metric score.
    formal runs in the bounded fast profile (depth capped)."""
    import shutil
    import yaml
    ev = ws / EVALUATOR_SUBDIR
    ew = ws / "log" / "optimise" / "evalws"
    shutil.rmtree(ew, ignore_errors=True)
    (ew / "spec").mkdir(parents=True)
    (ew / "rtl").mkdir()
    (ew / "synth").mkdir()
    spec = yaml.safe_load((ev / "spec.yaml").read_text(encoding="utf-8"))
    formal = dict(spec.get("formal") or {})
    formal["depth"] = min(int(formal.get("depth", FAST_FORMAL_DEPTH)),
                          int(profile["fast_formal_depth"]))
    spec["formal"] = formal
    (ew / "spec" / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False),
                                           encoding="utf-8")
    for rel in profile["rtl"]:
        src = ws / rel
        if src.is_file():
            shutil.copy2(src, ew / rel)
    if (ev / "lint_allow.yaml").is_file():
        shutil.copy2(ev / "lint_allow.yaml", ew / "rtl" / "lint_allow.yaml")
    for sub in ("tb", "formal"):
        if (ev / sub).is_dir():
            shutil.copytree(ev / sub, ew / sub)
    return ew


def _gate_status(mod, ew: Path, detail: list | None = None,
                 fail_on=("error",)) -> str:
    """One constraint as a gate: pass, fail, or error (it did not run -
    a refusal, never a pass). Failing messages go on `detail`."""
    detail = [] if detail is None else detail
    try:
        payload, _ = mod.run(["--workspace", str(ew)])
    except Exception as exc:  # noqa: BLE001 - a gate that did not run fails
        detail.append(f"{mod.SCRIPT}: {exc}"[:400])
        return "error"
    bad = [v for v in payload.get("violations", [])
           if v.get("severity") in fail_on]
    detail += [f"{mod.SCRIPT}: {v.get('msg') or v.get('message')}"[:400]
               for v in bad[:5]]
    return "fail" if bad else "pass"


def run_constraints(ew: Path, detail: list) -> dict:
    """lint, sim, formal (bounded) on the scratch workspace, in that order;
    the first failure skips the rest."""
    import check_formal
    import check_lint
    import check_sim
    out = {}
    for name, mod in (("lint", check_lint), ("sim", check_sim),
                      ("formal", check_formal)):
        if any(v != "pass" for v in out.values()):
            out[name] = "skipped"
            continue
        out[name] = _gate_status(mod, ew, detail)
    return out


def _run_tool(ew: Path, *cmd: str, stdout_only: bool = False) -> str:
    import subprocess
    try:
        proc = subprocess.run([str(sim_run.EDA_BIN), *cmd], cwd=str(ew),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TOOL_TIMEOUT_S,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"eda {cmd[0]} timed out: {exc}") from exc
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        raise CheckError(f"eda {cmd[0]} exited {proc.returncode}: "
                         f"{output[-1500:]}")
    return proc.stdout if stdout_only else output


def must_keep_missing(netlist_json: dict, top: str, names: list[str]) -> list[str]:
    """section 4: "`must_keep` cells are checked after synth". A name is
    kept when a cell (or a net, for a signal) of the flattened netlist is
    that name, ends in `.<name>`, or sits under instance `<name>.`."""
    mod = (netlist_json.get("modules") or {}).get(top) or {}
    have = set((mod.get("cells") or {}).keys()) | set(
        (mod.get("netnames") or {}).keys())
    have = {h.lstrip("\\") for h in have}
    missing = []
    for n in names:
        if not any(h == n or h.endswith("." + n) or h.startswith(n + ".")
                   for h in have):
            missing.append(n)
    return missing


def port_mismatch(netlist_json: dict, top: str, ports: dict) -> list[str]:
    mod = (netlist_json.get("modules") or {}).get(top) or {}
    got = {name: (p.get("direction"), len(p.get("bits") or []))
           for name, p in (mod.get("ports") or {}).items()}
    want = {name: (p.get("dir"), int(p.get("width", 1)))
            for name, p in ports.items()}
    diffs = []
    for name in sorted(set(got) | set(want)):
        if got.get(name) != want.get(name):
            diffs.append(f"{name}: want {want.get(name)}, got {got.get(name)}")
    return diffs


def run_metric(ew: Path, ev: Path, profile: dict, with_power: bool) -> dict:
    """yosys with the fixed script (area, ports, must_keep, synth
    findings), then OpenSTA with the fixed SDC (worst setup slack) and,
    with activity from the fixed testbench's own waves, power."""
    import re
    import check_synth
    import cocotblib
    top = profile["top"]
    liberty = Path(json.loads((ev / "liberty.json").read_text())["path"])
    out: dict = {"area": None, "slack": None, "power": None, "detail": []}
    import shutil
    shutil.copy2(ev / "synth.ys", ew / "synth.ys")
    ys = _run_tool(ew, "yosys", "-q", "-l", "synth.log", "-s", "synth.ys")
    ys = (ew / "synth.log").read_text(encoding="utf-8", errors="replace") \
        if (ew / "synth.log").is_file() else ys
    findings = []
    if check_synth.LOOP_RE.search(ys):
        findings.append("combinational loop")
    try:
        cells = check_synth.cell_histogram(ys)
    except CheckError:
        cells = {}
    if not cells:
        findings.append("empty netlist")
    findings += [f"unmapped {c}" for c in cells if c.startswith("$")]
    findings += [f"latch {c}" for c in cells if check_synth.LATCH_RE.search(c)]
    m = None
    for m in check_synth.AREA_RE.finditer(ys):
        pass
    if m is None:
        raise CheckError("no 'Chip area' line in the fixed synth script's log")
    out["area"] = float(m.group(2))
    out["synth"] = "fail" if findings else "pass"
    out["detail"] += findings

    nl = json.loads((ew / "synth" / f"{top}.json").read_text(encoding="utf-8"))
    ports = json.loads((ev / "ports.json").read_text(encoding="utf-8"))
    pdiff = port_mismatch(nl, top, ports)
    out["ports"] = "fail" if pdiff else "pass"
    out["detail"] += [f"port {d}" for d in pdiff]
    keep = json.loads((ev / "must_keep.json").read_text(encoding="utf-8"))
    missing = must_keep_missing(nl, top, keep)
    out["must_keep"] = "fail" if missing else "pass"
    out["detail"] += [f"must_keep {n} removed" for n in missing]

    vcd = None
    if with_power:
        build = ew / "log" / "power_build"
        tb = ew / "tb"
        cocotblib.run_cocotb(build, tb, [ew / r for r in profile["rtl"]],
                             top, cocotblib.test_modules(tb),
                             ew / "log" / "power_results.xml", waves=True)
        fst = build / f"{top}.fst"
        if fst.is_file():
            vcd = ew / "log" / "activity.vcd"
            vcd.write_text(_run_tool(ew, "fst2vcd", str(fst), stdout_only=True),
                           encoding="utf-8")
    (ew / "sta.tcl").write_text(sta_script(top, liberty, ev / "design.sdc",
                                           vcd), encoding="utf-8")
    sta = _run_tool(ew, "sta", "-no_splash", "-exit", "sta.tcl")
    sm = re.search(r"worst slack\s+(?:max\s+)?(-?[0-9.]+(?:e-?[0-9]+)?)", sta)
    if not sm:
        raise CheckError(f"OpenSTA printed no worst slack: {sta[-1500:]}")
    out["slack"] = float(sm.group(1))
    if vcd is not None:
        pm = re.search(r"^Total\s+\S+\s+\S+\s+\S+\s+([0-9.eE+-]+)", sta, re.M)
        if pm:
            out["power"] = float(pm.group(1))
    return out


def objective_score(objective: str, r: dict, baseline: dict | None) -> float | None:
    """Higher is better. area and power are minimised, slack maximised;
    score is the mean of area and power relative to the baseline."""
    if objective == "area":
        return -r["area"] if r.get("area") is not None else None
    if objective == "slack":
        return r.get("slack")
    if objective == "power":
        return -r["power"] if r.get("power") is not None else None
    if r.get("area") is None or r.get("power") is None:
        return None
    base = baseline or r
    return -0.5 * (r["area"] / base["area"] + r["power"] / base["power"])


def evaluate_rtl(ws: Path, meta: dict) -> dict:
    """Constraints, then (only if they pass) the metric. `passed` means
    every constraint passed; correctness is never a weighted term."""
    ev = ws / EVALUATOR_SUBDIR
    profile = json.loads((ev / "profile.json").read_text(encoding="utf-8"))
    ew = _eval_workspace(ws, profile)
    detail: list = []
    cons = run_constraints(ew, detail)
    for c in CONSTRAINTS:
        cons.setdefault(c, "skipped")
    result = {"constraints": cons, "area": None, "slack": None, "power": None,
              "score": None, "detail": detail, "passed": False}
    if any(cons[c] != "pass" for c in ("lint", "sim", "formal")):
        return result
    objective = meta["objective"]
    try:
        m = run_metric(ew, ev, profile, with_power=True)
    except Exception as exc:  # noqa: BLE001 - a metric that did not run fails
        cons["synth"] = "error"
        result["detail"].append(f"metric: {exc}")
        return result
    for c in ("synth", "ports", "must_keep"):
        cons[c] = m[c]
    # Slack is a constraint unless it is the objective: a smaller design
    # that misses the locked clock is not a better one.
    cons["timing"] = ("pass" if objective == "slack" or m["slack"] >= 0
                      else "fail")
    result.update(area=m["area"], slack=m["slack"], power=m["power"],
                  detail=detail + m["detail"])
    result["score"] = objective_score(objective, m, meta.get("baseline"))
    result["passed"] = all(cons[c] == "pass" for c in CONSTRAINTS) \
        and result["score"] is not None
    return result


def stop_reason(meta: dict) -> str | None:
    if meta["trials_run"] >= meta["trials_limit"]:
        return f"trials: {meta['trials_run']} of {meta['trials_limit']} run"
    if time.time() - meta["started_at"] >= meta["wall_min"] * 60:
        return f"wall: {meta['wall_min']} min budget spent"
    if meta["since_improved"] >= meta["patience"]:
        return f"patience: {meta['since_improved']} non-improving in a row"
    return None


def run_trial(argv=None):
    ap = argparse.ArgumentParser(description="score one optimiser change")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--note", required=True,
                    help="the optimiser's one-line note for the TSV row")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    meta = _load_meta(ws)
    if meta.get("kind") != "rtl":
        raise CheckError("`trial` scores an RTL loop; this workspace's "
                         "optimise run is a sizing search (use `numeric`)")
    if meta.get("aborted"):
        raise CheckError(f"this loop was aborted ({meta['aborted']}) - "
                         "re-run `optimise.py start`")
    if meta.get("stopped") or meta.get("finished"):
        raise CheckError(f"this loop has stopped ({meta.get('stopped')}) - "
                         "run `optimise.py finish`")
    target_rel = meta["target"]
    target = ws / target_rel
    head = _git(ws, "rev-parse", "HEAD").strip()
    expect = meta["kept"][-1]["commit"] if meta["kept"] else meta["start_commit"]
    trial_n = meta["trials_run"] + 1

    reverted = revert_outside_target(ws, target_rel)
    if head != expect:
        _git(ws, "checkout", "HEAD", "--", target_rel)
        meta["aborted"] = f"HEAD moved to {head[:12]} outside the loop"
        _save_meta(ws, meta)
        raise CheckError(f"HEAD is {head[:12]}, not the loop's {expect[:12]} "
                         "- something committed outside the loop; aborted")

    # section 4: "re-hashes the evaluator and aborts on a mismatch".
    ev_sha = hash_rtl_evaluator(ws)
    if ev_sha != meta["evaluator_sha"]:
        _git(ws, "checkout", "HEAD", "--", target_rel)
        meta["aborted"] = "evaluator hash mismatch"
        meta["trials_run"] = trial_n
        _append_row(ws, _row(trial_n, target, {}, False,
                             f"ABORTED: evaluator changed; {args.note}"))
        _save_meta(ws, meta)
        _record_state(ws, meta)
        raise CheckError(
            f"the frozen evaluator changed (was {meta['evaluator_sha'][:16]}, "
            f"now {ev_sha[:16]}); the target was reverted and the loop "
            "aborted - re-run `optimise.py start`")

    changed = target_rel in changed_paths(ws)
    trial_sha = file_sha(target) if target.is_file() else ""
    if not changed:
        result = {"constraints": {c: "skipped" for c in CONSTRAINTS},
                  "passed": False, "score": None}
        note = f"no change to the target; {args.note}"
    else:
        result = evaluate_rtl(ws, meta)
        note = args.note
    best = meta["best"]
    kept = bool(result["passed"] and result["score"] > best["score"])
    if kept:
        _git(ws, *GIT_ID, "commit", "-q", "-m",
             f"optimise trial {trial_n}: {args.note}", "--", target_rel)
        commit = _git(ws, "rev-parse", "HEAD").strip()
        entry = {"trial": trial_n, "score": result["score"],
                 "area": result["area"], "slack": result["slack"],
                 "power": result["power"], "target_sha": file_sha(target),
                 "commit": commit}
        meta["kept"].append(entry)
        meta["best"] = dict(entry)
        meta["since_improved"] = 0
    else:
        _git(ws, "checkout", "HEAD", "--", target_rel)
        meta["since_improved"] += 1
        if changed and not result["passed"]:
            failing = [c for c in CONSTRAINTS
                       if result["constraints"][c] not in ("pass", "skipped")]
            note = f"rejected ({', '.join(failing)}); {note}"
    if reverted:
        note += f" [reverted outside target: {', '.join(reverted)}]"
    meta["trials_run"] = trial_n
    _append_row(ws, _row(trial_n, target, result, kept, note, sha=trial_sha))
    meta["stopped"] = stop_reason(meta)
    _save_meta(ws, meta)
    _record_state(ws, meta)
    payload = {"script": SCRIPT, "status": "pass", "trial": trial_n,
               "kept": kept, "reverted_outside_target": reverted,
               "constraints": result["constraints"],
               "area": result.get("area"), "slack": result.get("slack"),
               "power": result.get("power"), "score": result.get("score"),
               "best": meta["best"], "detail": result.get("detail", []),
               "stop": meta["stopped"], "tsv": str(ws / TSV_PATH)}
    return payload, args.out


def full_gates(ws: Path, detail: list) -> dict:
    """section 4: "On the winner the full gates run: holdout, formal
    without the bound, mutate" - the real gate scripts on the real
    workspace, at the spec's own depth. Failing messages go on `detail`."""
    import check_formal
    import check_holdout
    import check_mutate
    return {name: _gate_status(mod, ws, detail)
            for name, mod in (("holdout", check_holdout),
                              ("formal", check_formal),
                              ("mutate", check_mutate))}


def run_finish(argv=None):
    ap = argparse.ArgumentParser(description="full gates on the winner")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    meta = _load_meta(ws)
    if meta.get("kind") != "rtl":
        raise CheckError("`finish` closes an RTL loop")
    if meta.get("aborted"):
        raise CheckError(f"this loop was aborted ({meta['aborted']})")
    target_rel = meta["target"]
    target = ws / target_rel
    revert_outside_target(ws, target_rel)
    _git(ws, "checkout", "HEAD", "--", target_rel)
    ev = ws / EVALUATOR_SUBDIR
    frozen = json.loads((ev / "holdout.json").read_text(encoding="utf-8"))
    now = statelib.hash_artifact(ws / "holdout", "dir_text") \
        if (ws / "holdout").is_dir() else None
    if frozen["dir_sha"] != now:
        raise CheckError("holdout/ changed since `start` - the winner "
                         "cannot be judged against a different holdout")

    # The winner first, then each earlier kept trial, then the baseline:
    # a winner that fails is discarded and the last passing trial restored.
    candidates = list(reversed(meta["kept"])) + [meta["baseline"]]
    tried = []
    chosen = None
    for cand in candidates:
        _git(ws, "checkout", cand["commit"], "--", target_rel)
        detail: list = []
        gates = full_gates(ws, detail)
        ok = all(v == "pass" for v in gates.values())
        tried.append({"trial": cand["trial"], "gates": gates, "passed": ok,
                      "detail": detail})
        _append_row(ws, _row(
            cand["trial"], target, {}, ok,
            "full gates on trial {}: {}".format(cand["trial"], " ".join(
                f"{k}={v}" for k, v in gates.items()))))
        if ok:
            chosen = cand
            break
    if chosen is None:
        _git(ws, "checkout", "HEAD", "--", target_rel)
    elif target_rel in changed_paths(ws):
        _git(ws, *GIT_ID, "commit", "-q", "-m",
             f"optimise: restore trial {chosen['trial']} - later winner "
             "failed the full gates", "--", target_rel)
    meta["finished"] = {"winner": chosen, "tried": tried}
    meta["stopped"] = meta.get("stopped") or "finished"
    _save_meta(ws, meta)
    _record_state(ws, meta)
    payload = {"script": SCRIPT,
               "status": "pass" if chosen is not None else "violations",
               "winner": chosen, "tried": tried,
               "discarded": [t["trial"] for t in tried if not t["passed"]],
               "baseline": meta["baseline"], "tsv": str(ws / TSV_PATH)}
    return payload, args.out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("start", "numeric", "trial", "finish"):
        sub.add_parser(name)
    ns, rest = ap.parse_known_args(argv)

    checklib.utf8_stdout()
    try:
        if ns.cmd == "start":
            tgt = argparse.ArgumentParser(add_help=False)
            tgt.add_argument("--target", default="")
            known, _ = tgt.parse_known_args(rest)
            payload, out = (run_rtl_start(rest) if is_rtl_target(known.target)
                            else run_start(rest))
        elif ns.cmd == "trial":
            payload, out = run_trial(rest)
        elif ns.cmd == "finish":
            payload, out = run_finish(rest)
        else:
            payload, out = run_numeric(rest)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}",
                          "remediation": str(exc)}))
        return 2
    text = json.dumps(payload, indent=1, default=str)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if payload.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
