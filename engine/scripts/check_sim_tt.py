#!/usr/bin/env python
"""check_sim_tt.py - the sim_tt gate (docs/design.md 1.5, "### M8."):
sim_run.py at the single 'tt' (typical) corner.

    check_sim_tt.py --workspace DIR [--out FILE]

Pass criteria (gates.yaml): "Every .measure inside its bound." Fault this
gate must catch: "W and L swapped on a mirror's output device". A plain
wrapper over sim_run.run_workspace_benches(corner_names=["tt"]) - see that
module (and engine/lib/simlib.py) for how a measure is parsed, why the
ngspice exit code is never trusted, and the `{{PDK}}`/`{{CORNER}}`/
`{{TEMP_C}}`/`{{VDD}}`/`{{NETLIST}}`/`{{SIZING}}` deck template contract
every tb/*.cir bench is written against.
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

SCRIPT = "check_sim_tt"
DEFAULT_TIMEOUT = 60.0


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    result = sim_run.run_workspace_benches(
        ws, corner_names=["tt"], timeout=args.timeout, check="sim_tt")

    # stamp() hashes exactly this path as input_digest; gate.py's own
    # record_gate cross-checks that against invalidation.yaml's gate_inputs
    # kinds[0] for this gate ("netlist" for sim_tt).
    payload = checklib.report(SCRIPT, ws / "netlist", result["violations"],
                              top=result["top"], corners=result["corners"],
                              results=result["results"])
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
