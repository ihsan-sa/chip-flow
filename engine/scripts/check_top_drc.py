#!/usr/bin/env python
"""check_top_drc.py - msde's top_drc gate (docs/design.md 1.5 msde table:
"magic and klayout on the assembled GDS", 0 violations).

    check_top_drc.py --workspace MSDE_WS [--out FILE]

Runs M4's drc recipe (check_drc.run: magic's DRC deck and klayout's
gf180mcu.drc) on the GDS top_harden left at top/harden/runs/run/final/gds -
the standard cells and the analog macro's own geometry in one layout. Refuses
when top_harden has not produced that GDS.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "lib"))
import checklib  # noqa: E402
import check_drc  # noqa: E402
import check_top_harden  # noqa: E402

SCRIPT = "check_top_drc"


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="msde block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    top = ws / check_top_harden.TOP_DIR
    if not (top / "spec" / "spec.yaml").is_file():
        raise checklib.CheckError(f"no assembled top at {top} - has the "
                                  "top_harden gate run?")
    inner, _out = check_drc.run(["--workspace", str(top)])
    for v in inner["violations"]:
        v["check"] = "top_drc"
    facts = {k: inner[k] for k in ("top", "magic_count", "klayout_count")}
    payload = checklib.report(
        SCRIPT, top / "harden" / "runs" / "run" / "final" / "gds",
        inner["violations"], **facts)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
