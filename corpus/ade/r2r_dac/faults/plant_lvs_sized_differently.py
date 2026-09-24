"""plant_lvs_sized_differently.py - ade/r2r_dac `lvs` fault, gates.yaml's
own "a device sized differently in the generator than in the netlist":
gen_r2r_dac.py draws both 2R legs at 20um while netlist/r2r_dac.cir, at
the rung's sizing, says 10um. Every pad still lands where the resistors
end, so the ladder stays connected and DRC-clean; only netgen's rm1
property comparison (r_length to 1%) sees it.
"""
from __future__ import annotations

from pathlib import Path

OLD = "R2_LENGTH = 10.0"
NEW = "R2_LENGTH = 20.0"


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_r2r_dac.py"
    text = gen_path.read_text(encoding="utf-8")
    if OLD not in text:
        raise RuntimeError(
            f"{gen_path}: {OLD} was not found as expected - "
            "gen_r2r_dac.py's own source shape changed")
    gen_path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
