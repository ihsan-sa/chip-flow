"""plant_drc_offgrid.py - ade/r2r_dac `drc` fault (docs/design.md 1.5,
"### M9."; docs/spikes/glayout.md). Patches layout/gen_r2r_dac.py's own
final `return layoutlib.finalize(...)` line to add one extra shape AFTER
finalize()'s own 5nm grid-snap (pcell_utilities.snap_to_grid) has already
run - the only way an off-grid shape can survive to the written GDS, since
every generator's own geometry goes through that snap. Placed far from any
real geometry (x=20) so ONLY klayout's own *_OFFGRID rule fires, not a
spacing rule - reproducing docs/spikes/glayout.md's own finding that this
class of bug is invisible to magic (which silently re-snaps on load) and
caught only by klayout's real GF180 deck.
"""
from __future__ import annotations

from pathlib import Path

OLD = '    return layoutlib.finalize(top, "r2r_dac", labels)\n'
NEW = (
    '    comp = layoutlib.finalize(top, "r2r_dac", labels)\n'
    '    # off-grid on purpose (20.1233 is not a multiple of 0.005) - added\n'
    '    # AFTER finalize()\'s own grid-snap, isolated far from real\n'
    '    # geometry so no other rule fires alongside *_OFFGRID.\n'
    '    comp.add_polygon([(20.1233, 20.1233), (20.1733, 20.1233),\n'
    '                      (20.1733, 20.1733), (20.1233, 20.1733)], layer=L1)\n'
    '    return comp\n'
)


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_r2r_dac.py"
    text = gen_path.read_text(encoding="utf-8")
    if OLD not in text:
        raise RuntimeError(
            f"{gen_path}: final return line not found as expected - "
            "gen_r2r_dac.py's own source shape changed")
    gen_path.write_text(text.replace(OLD, NEW), encoding="utf-8")
