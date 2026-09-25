"""Fault: an analog macro too large for the tile (gates.yaml's msde
top_harden row). Wraps the analog side's own layout generator so the cell
it returns carries one extra Metal1 box 400 um wide - wider than the TT
tile (346.64 um) - and changes nothing else. top_harden must report
macro_too_large before it spends a harden on it."""
from pathlib import Path

WRAP = '''

# --- planted by faults/plant_top_harden.py: a macro wider than the tile ---
_planted_generate = generate


def generate():
    comp = _planted_generate()
    comp.add_polygon([(0, 0), (400, 0), (400, 1), (0, 1)], layer=(34, 0))
    return comp
'''


def plant(ws: Path) -> None:
    gens = sorted((ws / "analog" / "layout").glob("gen_*.py"))
    if len(gens) != 1:
        raise RuntimeError(f"plant_top_harden.py: expected one analog "
                           f"layout generator, found {gens}")
    gens[0].write_text(gens[0].read_text(encoding="utf-8") + WRAP,
                       encoding="utf-8")
