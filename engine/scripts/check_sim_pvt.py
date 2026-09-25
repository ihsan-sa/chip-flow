#!/usr/bin/env python
"""check_sim_pvt.py - the sim_pvt gate (docs/design.md 1.5, "### M8."):
sim_run.py over the block's corner set (design.md 5: "the default is the
four extremes with typical, never fewer").

    check_sim_pvt.py --workspace DIR [--out FILE]

Pass criteria (gates.yaml): "Every measure inside its bound at every
corner." Fault this gate must catch: "meets at typical, loses headroom at
slow and hot" - engine/reference/corners.yaml's own `ss` default corner
(125 C, -10% supply) is exactly that combination.

Corner set: spec.yaml's top-level `corners` field, expanded by
corners.spec_corners() ("default" when absent) - that function's docstring
has the forms. A list is UNIONED with the default five, never used to
replace them - design.md 5's "never fewer" means what it says, so a spec
cannot skip a default corner (say, drop `ss` and have 'meets at typical,
loses headroom at slow and hot' go uncaught) by simply not naming it. A
`{grid: ...}` replaces the five with a PVT grid the spec declares, and
corners.grid_corners() refuses one that does not still span typical/ss/ff
and both temperature extremes.
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
import sim_run  # noqa: E402
import speclib  # noqa: E402

SCRIPT = "check_sim_pvt"
DEFAULT_TIMEOUT = 60.0


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    corner_list = corners_mod.spec_corners(corners_mod.load(),
                                           spec.get("corners", "default"))

    result = sim_run.run_workspace_benches(
        ws, corners=corner_list, timeout=args.timeout, check="sim_pvt")
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
