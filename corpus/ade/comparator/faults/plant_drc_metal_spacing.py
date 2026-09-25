"""plant_drc_metal_spacing.py - ade/comparator `drc` fault, gates.yaml's own
"a planted spacing violation": each latch column's gate-strip via moves
GATE_VIA_SHIFT towards the drain strip beside it instead of away from it,
so its 0.40um metal1 landing (one output) sits 0.18um from the drain strip
(the other output), under metal1's 0.23um minimum spacing (M1.2a). Both
nets stay wired as before, so only the metal rules can see it.
"""
from __future__ import annotations

from pathlib import Path

OLD = '        shift = -GATE_VIA_SHIFT if n["cx"] < 0 else GATE_VIA_SHIFT\n'
NEW = '        shift = GATE_VIA_SHIFT if n["cx"] < 0 else -GATE_VIA_SHIFT\n'


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_comparator.py"
    text = gen_path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise RuntimeError(
            f"{gen_path}: the line this fault edits was not found once - "
            "gen_comparator.py's own source shape changed")
    gen_path.write_text(text.replace(OLD, NEW), encoding="utf-8")
