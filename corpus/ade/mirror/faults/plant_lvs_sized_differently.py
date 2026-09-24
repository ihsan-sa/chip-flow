"""plant_lvs_sized_differently.py - ade/mirror `lvs` fault, gates.yaml's
own "a device sized differently in the generator than in the netlist":
gen_mirror.py draws xmout at W=6u while netlist/mirror.cir says 8u. The
wiring reads each pad off the drawn cell, so the layout stays connected
and DRC-clean; the topology still matches and only netgen's property
comparison under the PDK's setup (w, l to 1%) catches it.
"""
from __future__ import annotations

from pathlib import Path

OLD = "MOUT_W = 8.0\n"
NEW = "MOUT_W = 6.0\n"


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_mirror.py"
    text = gen_path.read_text(encoding="utf-8")
    if OLD not in text:
        raise RuntimeError(
            f"{gen_path}: MOUT_W = 8.0 was not found as expected - "
            "gen_mirror.py's own source shape changed")
    gen_path.write_text(text.replace(OLD, NEW), encoding="utf-8")
