#!/usr/bin/env python
"""faults.py - corpus fault verification (docs/design.md section 3, "###
M2."). New at M2 (docs/design.md 1.3: "New: ... faults.py (corpus faults
and per-run mutants) ...").

    faults.py --skill vde [--rung NAME] [--gates PATH] [--out FILE]

For every rung under `corpus/<skill>/` (or just `--rung NAME`): copies the
rung into a scratch workspace and confirms the UNTOUCHED reference passes
every gate its `faults/manifest.yaml` names; then, for each manifest entry,
makes a FRESH scratch copy, imports its `plant.py` and calls `plant(ws)`,
runs the ONE named gate, and checks the result against `expect` ({status:
pass|fail, kind: <a kind among the failing findings>}) - "the suite passes
when every fault is caught with the expected finding and the untouched
reference passes every gate" (docs/design.md section 3).

Runs gate.py's own evaluate() in-process (gate.run_report_for_gate +
gate.evaluate, docs/design.md 1.5) rather than a `gate.py` subprocess: this
script already runs inside `eda python`, so the check scripts' own
environment is already live, and faults.py calls several gates several
times each - the mutate gate alone is the corpus's slowest, so avoiding a
second interpreter start per call is not just tidiness.

CLI/exit contract: checklib's (argparse, JSON to stdout or --out, exit 0
every fault caught as expected, 1 a mismatch, 2 error).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
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
import gate  # noqa: E402
import state as state_mod  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "faults"
CORPUS = REPO / "corpus"
DEFAULT_GATES = ENGINE / "reference" / "gates.yaml"
COPY_SUBDIRS = ("rtl", "netlist", "tb", "holdout", "formal", "sizing",
               "layout", "layout_ref")
# formal: M3. sizing: M8 (docs/design.md "### M8.") - an ade rung's own
# sizing/sizing.yaml (section 4's optimise target) is a real per-block
# artifact (already in state.py's SUBDIRS and invalidation.yaml's ade
# artifact_kinds), so a scratch workspace copy must carry it exactly like
# tb/'s bounds sidecars - a gate whose bench references `{{SIZING}}`
# (engine/lib/simlib.py) with no sizing/sizing.yaml on disk gets an
# undefined-parameter ngspice error, not a silent default. layout,
# layout_ref: M9 - see the mkdir note just below for layout_ref's own
# scaffolding gap.


def load_manifest(rung_dir: Path) -> dict:
    import yaml
    p = rung_dir / "faults" / "manifest.yaml"
    if not p.is_file():
        raise CheckError(f"no faults manifest at {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("faults"):
        raise CheckError(f"{p}: no non-empty 'faults' list")
    return data


def make_scratch_workspace(tmp_root: Path, rung_dir: Path, skill: str,
                          rung: str, block: str | None = None) -> Path:
    """A fresh state.py-initialized workspace holding a byte-for-byte copy
    of the rung's own spec.md/spec.yaml and artifact directories - never the
    corpus itself, which stays read-only source of truth."""
    ws = tmp_root / rung
    if ws.exists():
        shutil.rmtree(ws)
    state_mod.State.init(ws, skill, block or rung)
    # corpus layout is flat (spec.md, spec.yaml at the rung root, docs/
    # design.md 1.1's corpus table); a workspace nests them under spec/
    # (state.py init's own SUBDIRS, docs/design.md 1.4) - this copy is
    # where the two layouts meet.
    for name in ("spec.md", "spec.yaml"):
        src = rung_dir / name
        if src.is_file():
            shutil.copy2(src, ws / "spec" / name)
    # M10 (msde): interface.yaml and the two per-side specs `split` checks
    # against it (docs/design.md section 5: "two specs carrying the same
    # entries") live at the rung root, same as spec.md/spec.yaml above -
    # but land at the WORKSPACE ROOT, not under spec/, matching
    # invalidation.yaml artifact_kinds' `interface` path (a top-level
    # artifact of an msde block, not a per-side spec). Harmless no-op for
    # vde/ade rungs, which never have these files.
    for name in ("interface.yaml", "digital_spec.yaml", "analog_spec.yaml"):
        src = rung_dir / name
        if src.is_file():
            shutil.copy2(src, ws / name)
    for sub in COPY_SUBDIRS:
        src_dir = rung_dir / sub
        if not src_dir.is_dir():
            continue
        # state.py init's own SUBDIRS scaffold does not include "layout_ref"
        # (M9's post-layout benches, not one of docs/design.md 1.4's named
        # workspace dirs) - mkdir here rather than teach state.py a
        # corpus-only subdir.
        (ws / sub).mkdir(parents=True, exist_ok=True)
        for f in src_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, ws / sub / f.name)
    # M10: an msde rung carries its two sides as rung-shaped trees at
    # digital/ and analog/; each becomes a nested workspace of its own skill
    # (check_release.NESTED), its block named by its spec.yaml's `top` - the
    # name its layout generator and netlist carry.
    if skill == "msde":
        import yaml
        for side, side_skill in (("digital", "vde"), ("analog", "ade")):
            src = rung_dir / side
            if (src / "spec.yaml").is_file():
                spec = yaml.safe_load((src / "spec.yaml").read_text(
                    encoding="utf-8")) or {}
                make_scratch_workspace(ws, src, side_skill, side,
                                       block=spec.get("top") or side)
    return ws


def run_gate(ws: Path, gate_name: str, gates: dict, skill: str) -> tuple[dict, dict]:
    """(report, result) - report is the raw check_<tool>.py payload, result
    is gate.evaluate()'s pass/fail verdict over it."""
    rows = gates.get(skill) or {}
    row = rows.get(gate_name)
    if row is None:
        raise CheckError(f"unknown gate {gate_name!r} for skill {skill!r}")
    report = gate.run_report_for_gate(row, ws)
    result = gate.evaluate(gate_name, row, report)
    return report, result


def check_expect(fault_name: str, result: dict, expect: dict) -> list[str]:
    problems = []
    want_status = expect.get("status")
    if want_status and result.get("status") != want_status:
        problems.append(f"{fault_name}: expected gate status {want_status!r}, "
                        f"got {result.get('status')!r}")
    want_kind = expect.get("kind")
    if want_kind:
        kinds = {v.get("kind") for v in result.get("failing", [])}
        if want_kind not in kinds:
            problems.append(f"{fault_name}: expected a failing finding of "
                            f"kind {want_kind!r}, got {sorted(k for k in kinds if k)}")
    return problems


def import_plant(rung_dir: Path, plant_rel: str):
    path = rung_dir / "faults" / plant_rel
    if not path.is_file():
        raise CheckError(f"plant script not found: {path}")
    spec = importlib.util.spec_from_file_location(
        f"plant_{rung_dir.name}_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "plant"):
        raise CheckError(f"{path} has no plant(ws) function")
    return mod.plant


def _ordered_gate_names(names: set[str], skill: str, gates: dict) -> list[str]:
    """Pipeline order (gates.yaml's own declaration order, via
    state.applicable_gate_order - phase then position), not alphabetical.
    M4 (docs/design.md "### M4."): a manifest naming "harden" alongside a
    gate that reads harden's own output (timing, drc, lvs, glsim, precheck)
    needs harden to run FIRST in the shared pre-check loop below; a plain
    alphabetical sort put "drc" ahead of "harden" and broke that."""
    order = [g for _, g in state_mod.applicable_gate_order(skill, gates)]
    index = {g: i for i, g in enumerate(order)}
    return sorted(names, key=lambda g: index.get(g, len(order)))


def run_rung(tmp_root: Path, rung_dir: Path, skill: str, gates: dict) -> dict:
    rung = rung_dir.name
    manifest = load_manifest(rung_dir)
    problems: list[str] = []
    gate_timings: dict[str, float] = {}

    # the untouched reference must pass every gate this manifest exercises -
    # except `release`, whose own "untouched" story is fabricated entirely
    # inside its own plant script (record a full pipeline pass against the
    # CURRENT files, then edit the RTL so it goes stale - the same trick
    # tests/test_check_release.py's pass_every_gate uses). A bare scratch
    # copy with nothing recorded in state.json yet can never pass `release`
    # (every OTHER gate reads as "no recorded result" there too), so
    # requiring that here would refuse every release fault regardless of
    # what it actually plants.
    clean_ws = make_scratch_workspace(tmp_root, rung_dir, skill, rung)
    gate_names = _ordered_gate_names(
        {f["gate"] for f in manifest["faults"]} - {"release"}, skill, gates)
    for gname in gate_names:
        t0 = time.monotonic()
        _report, result = run_gate(clean_ws, gname, gates, skill)
        gate_timings[gname] = round(time.monotonic() - t0, 2)
        if result["status"] != "pass":
            problems.append(f"{rung}: untouched reference fails gate "
                            f"{gname!r}: {result.get('failing')}")

    for entry in manifest["faults"]:
        name, gname = entry["name"], entry["gate"]
        plant = import_plant(rung_dir, entry["plant"])
        ws = make_scratch_workspace(tmp_root, rung_dir, skill, rung)
        # a plant may steer its gate through an env var (plant_precheck.py's
        # CHIP_FLOW_PRECHECK_TOP_OVERRIDE); it must not outlive that gate
        # and leak into the next fault's run in this same process.
        saved_env = dict(os.environ)
        try:
            plant(ws)
            _report, result = run_gate(ws, gname, gates, skill)
        finally:
            os.environ.clear()
            os.environ.update(saved_env)
        if gname == "mutate":
            gate_timings.setdefault("mutate_fault", round(
                (_report or {}).get("wall_s", 0.0), 2))
        problems.extend(check_expect(f"{rung}/{name}", result,
                                     entry.get("expect", {})))
        # A holdout fault must be invisible to the visible tb/ suite - that
        # is the whole point of the `holdout` gate (gates.yaml: "... which
        # the visible tests do not look"). Without this, a fault that also
        # breaks sim (like an earlier version of plant_holdout.py's own
        # delayed-reset fault, docs/design.md section 3) would still satisfy
        # check_expect above and never be caught: it demonstrates the wrong
        # thing, not "holdout catches what tb/ misses".
        if gname == "holdout":
            _sim_report, sim_result = run_gate(ws, "sim", gates, skill)
            if sim_result["status"] != "pass":
                problems.append(
                    f"{rung}/{name}: a holdout fault must leave the sim "
                    "gate passing (it must be invisible to tb/), but sim "
                    f"failed too: {sim_result.get('failing')}")

    return {"rung": rung, "problems": problems, "gate_wall_s": gate_timings}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skill", required=True, choices=state_mod.SKILLS)
    ap.add_argument("--rung", help="only this rung (default: every rung "
                    "under corpus/<skill>/)")
    ap.add_argument("--gates", default=str(DEFAULT_GATES))
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    gates = gate.load_gates(Path(args.gates))
    skill_dir = CORPUS / args.skill
    if not skill_dir.is_dir():
        raise CheckError(f"no corpus directory at {skill_dir}")
    rungs = ([skill_dir / args.rung] if args.rung
            else sorted(p for p in skill_dir.iterdir() if p.is_dir()))
    if not rungs:
        raise CheckError(f"no rungs under {skill_dir}")

    results = []
    with tempfile.TemporaryDirectory(prefix="chip-flow-faults-") as tmp:
        tmp_root = Path(tmp)
        for rung_dir in rungs:
            results.append(run_rung(tmp_root, rung_dir, args.skill, gates))

    violations = []
    for r in results:
        for problem in r["problems"]:
            violations.append(checklib.violation(
                "faults", "error", None, r["rung"], "fault_mismatch", [],
                problem, "faults.py"))

    payload = checklib.report(SCRIPT, skill_dir, violations, skill=args.skill,
                              rungs={r["rung"]: r["gate_wall_s"] for r in results})
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
