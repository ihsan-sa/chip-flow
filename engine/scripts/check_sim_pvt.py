#!/usr/bin/env python
"""check_sim_pvt.py - the sim_pvt gate (docs/design.md 1.5, "### M8."):
sim_run.py over the block's corner set (design.md 5: "the default is the
four extremes with typical, never fewer").

    check_sim_pvt.py --workspace DIR [--out FILE]

Pass criteria (gates.yaml): "Every measure inside its bound at every
corner." Fault this gate must catch: "meets at typical, loses headroom at
slow and hot" - engine/reference/corners.yaml's own `ss` default corner
(125 C, -10% supply) is exactly that combination.

Corner set: spec.yaml's top-level `corners` field (speclib.lint_spec_ade's
schema: "default" | "all" | a list of corner names) - "default" (the
implicit value when the field is absent) is corners.py's own
default_corners(), "all" is every corners.yaml axis combination, and a list
names an explicit subset/superset. Never fewer than the default five
(design.md 5) - a spec asking for "all" or a superset is honored, but
nothing here lets a spec ask for FEWER than default_corners() without
naming them explicitly (which is itself still "never fewer" than what it
declared).
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
import sim_run  # noqa: E402
import speclib  # noqa: E402

SCRIPT = "check_sim_pvt"
DEFAULT_TIMEOUT = 60.0


def corner_names_for_spec(spec: dict) -> list[str] | None:
    """None means "sim_run's own default" (corners.py's default_corners(),
    design.md 5's five curated points). A list names an explicit subset/
    superset of corners.yaml's default_corners entries by name - "all" is
    not accepted here (corners.yaml curates named points; a spec wanting
    every raw process x temp x supply combination names them explicitly,
    which add-corner - skills/ade/reference/tasks.yaml - is what extends)."""
    field = spec.get("corners", "default")
    return None if field == "default" else field


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    corners_field = corner_names_for_spec(spec)

    result = sim_run.run_workspace_benches(
        ws, corner_names=corners_field, timeout=args.timeout, check="sim_pvt")
    top, corner_list = result["top"], result["corners"]
    results, violations = result["results"], result["violations"]

    # stamp() hashes exactly this path as input_digest; gate.py's own
    # record_gate cross-checks that against invalidation.yaml's gate_inputs
    # kinds[0] for this gate ("netlist" for sim_pvt).
    payload = checklib.report(SCRIPT, ws / "netlist", violations, top=top,
                              corners=corner_list, results=results)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
