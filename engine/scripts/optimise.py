#!/usr/bin/env python
"""optimise.py - the optimise loop (docs/design.md section 4, "### M8."):
`start` freezes an evaluator, `numeric` runs scipy differential evolution
against it. New at M8: M7 (the agent-driven per-trial loop for /vde's RTL,
"### M7.") has not landed, so this is the analog half section 4 describes
("Analog sizing runs the same loop with `--target sizing/sizing.yaml`...
`optimise.py numeric`") built directly against sim_run.py rather than on
top of a pre-existing digital `trial` command - there is no `optimise.py
trial` here, and none of vde's synth/OpenSTA/VCD machinery applies to a
sizing search. `start`/`numeric` are the two verbs skills/ade/reference/
tasks.yaml's own `optimise` verb calls.

    optimise.py start --workspace DIR --target sizing/sizing.yaml
                [--objective margin|power] [--out FILE]
    optimise.py numeric --workspace DIR [--trials N] [--wall MIN]
                [--popsize N] [--maxiter N] [--seed N] [--out FILE]

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
found) or the very last row (the written winner).
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
    tsv_rows: list[dict] = []
    trial_counter = {"n": 0}
    best = {"score": None, "sizing": None}

    def sizing_from_x(x) -> dict:
        return {n: {"value": float(v), "min": start_sizing[n]["min"],
                   "max": start_sizing[n]["max"]} for n, v in zip(names, x)}

    def record(sizing: dict, score: float, note: str) -> None:
        n = trial_counter["n"]
        kept = best["score"] is None or score > best["score"]
        if kept:
            best["score"], best["sizing"] = score, sizing
        tsv_rows.append({"trial": n, "timestamp": round(time.time(), 3),
                         "target_sha": target_sha(sizing), "score": round(score, 6),
                         "kept": kept, "note": note})
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

    result = differential_evolution(
        objective, bounds_arr, seed=args.seed, popsize=args.popsize,
        maxiter=args.maxiter, polish=False, tol=1e-3)

    improved = best["sizing"] is not start_sizing and best["score"] > start_score
    if improved:
        write_sizing_yaml(target_path, best["sizing"])
    # else: nothing beat the start - the file is left exactly as it was
    # (the digital loop's own "otherwise git reverts it", applied once).
    kept_final = best["sizing"] if improved else start_sizing

    with open(tsv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TSV_FIELDS, delimiter="\t")
        for row in tsv_rows:  # `start`'s own write left only the header
            w.writerow(row)

    all_in_bounds = best["score"] is not None and best["score"] >= 0
    payload = {
        "script": SCRIPT, "status": "pass" if all_in_bounds else "violations",
        "trials": trial_counter["n"], "start_score": round(start_score, 6),
        "best_score": round(best["score"], 6) if best["score"] is not None else None,
        "improved": improved, "written": improved, "kept_sizing": kept_final,
        "tsv": str(tsv_path), "scipy_message": result.message,
    }
    return payload, args.out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start")
    sub.add_parser("numeric")
    ns, rest = ap.parse_known_args(argv)

    checklib.utf8_stdout()
    try:
        if ns.cmd == "start":
            payload, out = run_start(rest)
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
