"""plant_drc_metal_spacing.py - ade/mirror `drc` fault, gates.yaml's own
"a planted spacing violation": the vdd pad is pulled in to 0.12um from the
end of the vss bus, under metal1's 0.23um minimum spacing (M1.2a). Two
different nets, both legal on their own - only the metal rules the gate
now runs (layoutlib.DRC_DECKS keeps metal.rb on) can see it.
"""
from __future__ import annotations

from pathlib import Path

OLD = "    vdd_x = tap_x + TAP_SIZE + 1.0\n"
NEW = "    vdd_x = tap_x + 0.8 + 0.12  # 0.12um from the vss bus's end\n"


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_mirror.py"
    text = gen_path.read_text(encoding="utf-8")
    if OLD not in text:
        raise RuntimeError(
            f"{gen_path}: the vdd pad placement line was not found as "
            "expected - gen_mirror.py's own source shape changed")
    gen_path.write_text(text.replace(OLD, NEW), encoding="utf-8")
