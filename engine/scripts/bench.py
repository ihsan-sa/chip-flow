#!/usr/bin/env python
"""bench.py - per-stage benches on frozen fixtures (docs/design.md section 3,
"### M6.").

    bench.py --freeze --stage P4 --fixture NAME --from WS_OR_RUNG [--skill vde]
    bench.py --stage P4 --fixture NAME [--baseline | --compare] [--out FILE]

A fixture, `evals/fixtures/<stage>/<name>/`, is a workspace snapshot at a
stage boundary: `ws/` holds the block's artifact files and `fixture.json`
pins every one of them by sha256. `--freeze` makes one from `--from`, either
a block workspace (it has a state.json) or a corpus rung (spec.md at its
root, copied the way faults.py builds its scratch workspaces).

A bench copies the fixture into a scratch workspace and runs the stage's
gates on it: every gate gates.yaml puts in that phase for the fixture's
skill, except job gates (P6 names its own list below, since harden is one),
and vde P3, which scores a frozen testbench against the frozen RTL with
sim, mutate and cover. Each gate scores 1.0 on a pass; a failing mutate or
cover scores its kill rate or line coverage over the threshold it missed,
and any other failing gate scores 0. The composite is the mean. Metrics
beside it (kill rate, coverage, area, slack, finding counts) are recorded,
and wall time is informational only.

`--baseline` writes the score to the fixture's `baseline.json`. `--compare`
reads it and reports a finding (exit 1) when the composite is lower or a
gate the baseline passed now fails. Every bench also writes a dated result
under `evals/results/bench/`. A fixture whose files no longer match their
pins still runs, and each drifted file is a finding, so an edited fixture is
never scored as if it were the frozen one; `--baseline` refuses a drifted
fixture outright (re-freeze instead). A gate that could not run is exit 2,
never a score - except one that refuses after an earlier gate of the same
stage failed (mutate refuses RTL that fails its visible tests), which is
recorded as not run and scores 0, never a pass.

CLI/exit contract: checklib's (JSON out, exit 0 pass, 1 findings, 2 error).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import faults  # noqa: E402
import gate  # noqa: E402
import state as state_mod  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "bench"
DEFAULT_GATES = ENGINE / "reference" / "gates.yaml"
EVALS = REPO / "evals"
# the files a fixture carries: the artifact directories a gate reads, never
# state.json, runs/, log/, reports/ or snapshots, which are a run's history
# rather than its inputs.
FREEZE_DIRS = ("spec", "rtl", "netlist", "tb", "holdout", "formal", "synth",
               "harden", "sizing", "layout", "layout_ref")
FREEZE_ROOT_FILES = ("interface.yaml", "digital_spec.yaml", "analog_spec.yaml")
# stages whose gate list is not simply "the non-job gates of that phase"
STAGE_GATES = {
    ("vde", "P3"): ["sim", "mutate", "cover"],
    ("vde", "P6"): ["harden", "timing", "drc", "lvs"],
}
# a failing gate's partial score: (report field, threshold)
PARTIAL = {"mutate": ("kill_rate", 0.9), "cover": ("line_pct", 95.0)}
METRICS = ("kill_rate", "killed", "survived", "line_pct", "toggle_pct",
           "tests_passed", "area", "corners")
EPS = 1e-9


def rel_input(payload: dict) -> dict:
    """Results get committed, and this repo may go public: record the
    fixture path relative to the repo, never an absolute home path."""
    try:
        payload["input"] = Path(payload["input"]).resolve().relative_to(REPO).as_posix()
    except (KeyError, ValueError):
        payload["input"] = Path(payload.get("input", "")).name
    return payload


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_gates(skill: str, stage: str, gates: dict) -> list[str]:
    if (skill, stage) in STAGE_GATES:
        return list(STAGE_GATES[(skill, stage)])
    rows = gates.get(skill) or {}
    names = [g for g, row in rows.items()
             if row.get("phase") == stage and not row.get("job")]
    if not names:
        raise CheckError(f"no gates for skill {skill!r} at stage {stage!r} "
                         "in gates.yaml, and no bench stage list names it")
    return names


def fixture_dir(stage: str, name: str, root: Path) -> Path:
    return root / stage / name


def freeze(src: Path, dest: Path, skill: str | None, stage: str) -> dict:
    src = src.resolve()
    tmp = None
    if (src / "state.json").is_file():
        st = json.loads((src / "state.json").read_text(encoding="utf-8"))
        skill = skill or st.get("skill")
        block = st.get("block") or src.name
        ws = src
    elif (src / "spec.md").is_file():
        if not skill:
            skill = src.parent.name
        block = src.name
        tmp = tempfile.TemporaryDirectory(prefix="chip-flow-freeze-")
        ws = faults.make_scratch_workspace(Path(tmp.name), src, skill, block)
    else:
        raise CheckError(f"{src} is neither a block workspace (state.json) "
                         "nor a corpus rung (spec.md)")
    try:
        if dest.exists():
            shutil.rmtree(dest)
        files = {}
        for sub in FREEZE_DIRS:
            d = ws / sub
            if not d.is_dir():
                continue
            for f in sorted(d.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    rel = f.relative_to(ws).as_posix()
                    (dest / "ws" / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest / "ws" / rel)
                    files[rel] = sha256(f)
        for name in FREEZE_ROOT_FILES:
            if (ws / name).is_file():
                (dest / "ws").mkdir(parents=True, exist_ok=True)
                shutil.copy2(ws / name, dest / "ws" / name)
                files[name] = sha256(ws / name)
    finally:
        if tmp:
            tmp.cleanup()
    if not files:
        raise CheckError(f"nothing to freeze under {src}")
    try:
        source = src.relative_to(REPO).as_posix()
    except ValueError:
        source = src.name  # never an absolute home path in the repo
    meta = {"version": 1, "skill": skill, "block": block, "stage": stage,
            "source": source,
            "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "files": files}
    (dest / "fixture.json").write_text(json.dumps(meta, indent=1) + "\n",
                                       encoding="utf-8")
    return meta


def drift(fx: Path, meta: dict) -> list[str]:
    out = []
    for rel, want in sorted(meta["files"].items()):
        p = fx / "ws" / rel
        if not p.is_file():
            out.append(f"{rel}: missing")
        elif sha256(p) != want:
            out.append(f"{rel}: sha differs from its pin")
    pinned = set(meta["files"])
    for p in sorted((fx / "ws").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            rel = p.relative_to(fx / "ws").as_posix()
            if rel not in pinned:
                out.append(f"{rel}: not pinned")
    return out


def scratch_from_fixture(tmp_root: Path, fx: Path, meta: dict) -> Path:
    ws = tmp_root / meta["block"]
    state_mod.State.init(ws, meta["skill"], meta["block"])
    for rel in meta["files"]:
        src = fx / "ws" / rel
        if src.is_file():
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, ws / rel)
    return ws


def score_gate(name: str, report: dict, result: dict) -> float:
    if result["status"] == "pass":
        return 1.0
    field = PARTIAL.get(name)
    if field and isinstance(report.get(field[0]), (int, float)):
        return min(1.0, max(0.0, report[field[0]] / field[1])) * 0.999
    return 0.0


def bench(fx: Path, meta: dict, gates: dict) -> dict:
    names = stage_gates(meta["skill"], meta["stage"], gates)
    rows = gates.get(meta["skill"]) or {}
    per_gate = {}
    t_all = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="chip-flow-bench-") as tmp:
        ws = scratch_from_fixture(Path(tmp), fx, meta)
        for name in names:
            if name not in rows:
                raise CheckError(f"stage gate {name!r} is not in gates.yaml "
                                 f"for skill {meta['skill']!r}")
            t0 = time.monotonic()
            try:
                report = gate.run_report_for_gate(rows[name], ws)
                if report.get("status") == "error":
                    raise CheckError(report.get("error"))
            except Exception as exc:  # noqa: BLE001  a gate that did not run
                earlier = [g for g, v in per_gate.items() if v["status"] != "pass"]
                if not earlier:
                    raise CheckError(f"gate {name!r} could not run on fixture "
                                     f"{fx.name}: {exc}") from exc
                # mutate refuses RTL that already fails its visible tests:
                # a consequence of the failure already scored, so it scores
                # 0 as not run rather than hiding that failure behind exit 2
                per_gate[name] = {"status": "not_run", "score": 0.0,
                                  "failing": 0, "kinds": [],
                                  "why": f"refused after {', '.join(earlier)} "
                                         f"failed: {exc}",
                                  "metrics": {},
                                  "wall_s": round(time.monotonic() - t0, 2)}
                continue
            result = gate.evaluate(name, rows[name], report)
            per_gate[name] = {
                "status": result["status"],
                "score": round(score_gate(name, report, result), 4),
                "failing": len(result.get("failing", [])),
                "kinds": sorted({v.get("kind") for v in result.get("failing", [])
                                 if v.get("kind")}),
                "metrics": {k: report[k] for k in METRICS if k in report},
                "wall_s": round(time.monotonic() - t0, 2),
            }
    composite = sum(g["score"] for g in per_gate.values()) / len(per_gate)
    return {"composite": round(composite, 4), "gates": per_gate,
            "wall_s": round(time.monotonic() - t_all, 2)}


def compare(score: dict, base: dict) -> list[str]:
    out = []
    if score["composite"] < base["composite"] - EPS:
        out.append(f"composite {score['composite']} is below the baseline's "
                   f"{base['composite']}")
    for name, g in base["gates"].items():
        now = score["gates"].get(name)
        if g["status"] == "pass" and (now is None or now["status"] != "pass"):
            out.append(f"gate {name} passed at baseline and now "
                       f"{'did not run' if now is None or now['status'] == 'not_run' else 'fails'}"
                       + (f" ({', '.join(now['kinds'])})" if now and now["kinds"] else ""))
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", required=True, help="P3, P4, P5, P6 ...")
    ap.add_argument("--fixture", required=True, help="fixture name")
    ap.add_argument("--freeze", action="store_true",
                    help="make the fixture from --from instead of benching it")
    ap.add_argument("--from", dest="src", help="workspace or corpus rung to freeze")
    ap.add_argument("--skill", choices=state_mod.SKILLS,
                    help="with --freeze from a corpus rung (default: its parent dir)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--baseline", action="store_true",
                      help="write the score as the fixture's baseline.json")
    mode.add_argument("--compare", action="store_true",
                      help="fail on a lower composite than baseline.json")
    ap.add_argument("--fixtures-root", default=str(EVALS / "fixtures"))
    ap.add_argument("--results-dir", default=str(EVALS / "results" / "bench"))
    ap.add_argument("--gates", default=str(DEFAULT_GATES))
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    fx = fixture_dir(args.stage, args.fixture, Path(args.fixtures_root))
    if args.freeze:
        if not args.src:
            raise CheckError("--freeze needs --from <workspace or corpus rung>")
        meta = freeze(Path(args.src), fx, args.skill, args.stage)
        payload = checklib.report(SCRIPT, fx, [], mode="freeze",
                                  fixture=args.fixture, stage=args.stage,
                                  skill=meta["skill"], files=len(meta["files"]))
        return rel_input(payload), args.out

    meta_p = fx / "fixture.json"
    if not meta_p.is_file():
        raise CheckError(f"no fixture at {fx} (make one with --freeze --from)")
    meta = checklib.load_json(meta_p, "fixture")
    if meta.get("stage") != args.stage:
        raise CheckError(f"{meta_p} is a {meta.get('stage')} fixture, not {args.stage}")
    drifted = drift(fx, meta)
    if args.baseline and drifted:
        raise CheckError(f"fixture {args.fixture} has drifted from its pins "
                         f"({'; '.join(drifted[:3])}); re-freeze it before "
                         "writing a baseline")
    gates = gate.load_gates(Path(args.gates))
    score = bench(fx, meta, gates)

    violations = [checklib.violation("bench", "error", f"ws/{d.split(':')[0]}",
                                     None, "fixture_drift", [], d, "bench.py")
                  for d in drifted]
    base = None
    if args.baseline:
        (fx / "baseline.json").write_text(json.dumps(
            {"composite": score["composite"], "gates": score["gates"],
             "written": dt.date.today().isoformat()}, indent=1) + "\n",
            encoding="utf-8")
    elif args.compare:
        base_p = fx / "baseline.json"
        if not base_p.is_file():
            raise CheckError(f"no baseline at {base_p} (write one with --baseline)")
        base = checklib.load_json(base_p, "baseline")
        for problem in compare(score, base):
            violations.append(checklib.violation(
                "bench", "error", None, None, "below_baseline", [], problem,
                "bench.py"))

    mode = "baseline" if args.baseline else "compare" if args.compare else "score"
    payload = checklib.report(
        SCRIPT, fx, violations, mode=mode, fixture=args.fixture,
        stage=args.stage, skill=meta["skill"], block=meta["block"],
        composite=score["composite"],
        baseline_composite=base["composite"] if base else None,
        gates=score["gates"], wall_s=score["wall_s"])
    rel_input(payload)
    stamp = dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    res_dir = Path(args.results_dir)
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / f"{stamp}_{args.stage}_{args.fixture}_{mode}.json").write_text(
        json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
