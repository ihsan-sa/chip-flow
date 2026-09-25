"""plant_drc_missing_guard_ring.py - ade/comparator `drc` fault: removes the
P+ substrate tap left of the NFETs and leaves the vss routing in place - a
real "missing guard ring", no substrate tap near any NMOS. klayout's GF180
signoff deck reports it as DF.14 (a substrate tap within 20um of NCOMP).
"""
from __future__ import annotations

from pathlib import Path

OLD = '    tap = top.add_ref(layoutlib.psub_tap(TAP_SIZE))\n    tap.move((tap_x, 0.0))\n'
NEW = ''


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_comparator.py"
    text = gen_path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise RuntimeError(
            f"{gen_path}: the line this fault edits was not found once - "
            "gen_comparator.py's own source shape changed")
    gen_path.write_text(text.replace(OLD, NEW), encoding="utf-8")
