"""drc_top.py - section b of the macro_harden spike: DRC the hardened tile
(standard cells + the analog macro's own geometry) with magic and klayout,
by the exact recipes the M4 drc gate uses (engine/scripts/check_drc.py's
run_magic_drc / run_klayout_drc, imported, not copied).

    eda python drc_top.py FINAL_GDS TOPCELL WORK_DIR

Prints one JSON line: {"magic": N, "klayout": N, "klayout_by_rule": {...}}.
Exit 0 when both are 0, 1 when either is not, 2 when a tool did not finish.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "engine" / "scripts"))
sys.path.insert(0, str(REPO / "engine" / "lib"))
import check_drc  # noqa: E402
import layoutlib  # noqa: E402
from checklib import CheckError  # noqa: E402


def main() -> int:
    gds, top, work = Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    try:
        magic = check_drc.run_magic_drc(gds, top, work)
        klayout = check_drc.run_klayout_drc(gds, top, check_drc._pdk_root(), work)
    except CheckError as exc:
        print(json.dumps({"error": str(exc)[-1500:]}))
        return 2
    by_rule = collections.Counter(
        i["category"] for i in layoutlib.parse_drc_rdb(work / ".klayout_drc.lyrdb"))
    print(json.dumps({"magic": magic, "klayout": klayout,
                      "klayout_by_rule": dict(by_rule)}))
    return 0 if magic == 0 and klayout == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
