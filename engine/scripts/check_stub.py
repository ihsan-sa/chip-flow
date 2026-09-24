#!/usr/bin/env python
"""check_stub.py - placeholder tool for a gate not yet built (M1 skeleton).

Every gate gates.yaml registers points `tool: stub` at M1 (docs/design.md,
"### M1."): the registry validates now, and each milestone that builds a
real gate replaces its row's `tool` with the real check_<gate>.py stem. This
script is that placeholder - it always refuses, so a stub gate can never be
recorded as a pass (gate.py: "a gate whose tool could not run is exit 2,
never a pass").

CLI: check_stub.py --workspace DIR [--out FILE]
Exit: always 2 (the gate this stands in for has no check yet).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from checklib import CheckError  # noqa: E402
import checklib  # noqa: E402

SCRIPT = "check_stub"


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", help="block workspace (unused - every "
                    "stub refuses regardless of input)")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)
    raise CheckError(
        "not built: this gate has no check script yet - land it in its "
        "milestone (docs/design.md 6) and point the gate's gates.yaml row "
        "at the real check_<gate>.py")


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
