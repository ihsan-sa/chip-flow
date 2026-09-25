#!/usr/bin/env python
"""check_spec_lint_ade.py - /ade's spec_lint gate (docs/design.md 1.5,
"### M8."). A dedicated script rather than a skill-aware check_spec_lint.py:
that script is vde's own (M2), requires a non-empty `requirements` list and
validates `tt_pins`/`clock` - neither of which gates.yaml's ade spec_lint row
asks for ("Every measure has bounds and a corner set; supply and devices
declared."), and reusing it wholesale would force every analog block to also
carry digital-shaped fields it has no use for. Mirrors check_spec_lint.py's
own shape exactly otherwise.

    check_spec_lint_ade.py --workspace DIR [--out FILE]

Fault this gate must catch (gates.yaml): "a measure without bounds" - which
this script reads as covering BOTH halves of "bounds": spec.yaml's own
per-measure `bounds` dict (lint_spec_ade, unchanged) and, when tb/ already
has bench(es) written, whether each spec measure has a matching tb/*.bounds
.json sidecar bound and vice versa, each bound's optional `corners` equal
to its measure's own (speclib.lint_measures_vs_bench_bounds -
the bench-writer works in fresh context, per docs/design.md 1.3, so the two
declarations can drift apart with nothing else catching it). It also expands
the spec's `corners` field through corners.spec_corners(), so a `{grid: ...}`
sim_pvt would refuse is a `bad_corners` finding here, and a measure whose
own `corners` list names a corner outside that sweep is `measure_bad_corners`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import corners as corners_mod  # noqa: E402
import simlib  # noqa: E402
import speclib  # noqa: E402

SCRIPT = "check_spec_lint_ade"
SPEC_REL = "spec/spec.yaml"


def load_bench_bounds(ws: Path) -> dict[str, list[dict]]:
    """{bench filename: [bounds sidecar entries]} for every tb/*.cir with a
    matching tb/*.bounds.json sidecar - {} when tb/ does not exist yet or
    has none (spec_lint runs before any bench-writer step exists in
    skills/ade/reference/tasks.yaml's own `spec` verb; nothing to
    reconcile against yet is not itself a violation). Deliberately not
    sim_run.find_benches(): that raises when no bench exists at all, which
    is the ordinary, expected state right after spec-writer runs, not an
    error this gate should ever surface."""
    tb = ws / "tb"
    out: dict[str, list[dict]] = {}
    if not tb.is_dir():
        return out
    for cir in sorted(tb.glob("*.cir")):
        bounds_path = tb / f"{cir.stem}.bounds.json"
        if bounds_path.is_file():
            out[cir.name] = simlib.load_bounds(bounds_path)
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec_path = ws / SPEC_REL
    spec = speclib.load_spec(spec_path)
    violations = speclib.lint_spec_ade(spec, rel_path=SPEC_REL)
    if not any(v["kind"] == "bad_corners" for v in violations):
        # the shape is right; now expand it the way sim_pvt will, so a grid
        # that misses ss or 125 C is refused here, not at P4
        try:
            swept = {c["name"] for c in corners_mod.spec_corners(
                corners_mod.load(), spec.get("corners", "default"))}
        except checklib.CheckError as exc:
            swept = None
            violations.append(checklib.violation(
                "spec_lint", "error", SPEC_REL, None, "bad_corners", [],
                str(exc), "corners"))
        # a measure scoped to a corner the sweep never runs (`[tt]` under a
        # grid, whose typical corners are tt_<temp>c) would never be scored
        for m in spec.get("measures") or []:
            want = m.get("corners") if isinstance(m, dict) else None
            if swept is None or not isinstance(want, list):
                continue
            missing = [c for c in want if c not in swept]
            if missing:
                violations.append(checklib.violation(
                    "spec_lint", "error", SPEC_REL, None,
                    "measure_bad_corners", [m.get("name") or ""],
                    f"measure {m.get('name')!r} names corner(s) {missing} "
                    f"the spec's own sweep does not run: {sorted(swept)}",
                    "corners"))
    bench_bounds = load_bench_bounds(ws)
    violations += speclib.lint_measures_vs_bench_bounds(
        spec, bench_bounds, rel_path=SPEC_REL)
    # stamp() hashes exactly this path as input_digest; gate.py's own
    # record_gate cross-checks that against invalidation.yaml's gate_inputs
    # kinds[0] for this gate ("spec_yaml" for ade's spec_lint too).
    payload = checklib.report(SCRIPT, spec_path, violations)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
