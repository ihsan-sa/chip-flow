"""plant_lvs_sized_differently.py - ade/comparator `lvs` fault, gates.yaml's
own "a device sized differently in the generator than in the netlist":
gen_comparator.py draws xmtail at W=5u while the netlist's sizing says 6u.
The wiring reads each pad off the drawn cell, so the layout stays connected
and DRC-clean; only netgen's property comparison (w, l to 1%) catches it.
"""
from __future__ import annotations

from pathlib import Path

OLD = 'W_TAIL = 6.0\n'
NEW = 'W_TAIL = 5.0\n'


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_comparator.py"
    text = gen_path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise RuntimeError(
            f"{gen_path}: the line this fault edits was not found once - "
            "gen_comparator.py's own source shape changed")
    gen_path.write_text(text.replace(OLD, NEW), encoding="utf-8")
