"""plant_lvs_swapped_net.py - ade/comparator `lvs` fault, the swapped net: the
clk and vdd labels trade names, so the net on every PFET source and the
nwell taps is called "clk" and the net on the tail and reset gates "vdd".
The geometry is untouched; magic extracts the same eleven devices on
differently named nets than the netlist wires them, and netgen reports a
topology mismatch.
"""
from __future__ import annotations

from pathlib import Path

OLD_CLK = '    labels.append(("clk", -clk_x, (tail_y + mid_y["outn"]) / 2, L1LBL))\n'
NEW_CLK = '    labels.append(("vdd", -clk_x, (tail_y + mid_y["outn"]) / 2, L1LBL))\n'
OLD = '    labels.append(("vdd", ntap_in + 0.5, p_y0 + 0.5, L1LBL))\n'
NEW = '    labels.append(("clk", ntap_in + 0.5, p_y0 + 0.5, L1LBL))\n'


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_comparator.py"
    text = gen_path.read_text(encoding="utf-8")
    if text.count(OLD) != 1 or text.count(OLD_CLK) != 1:
        raise RuntimeError(
            f"{gen_path}: the line this fault edits was not found once - "
            "gen_comparator.py's own source shape changed")
    gen_path.write_text(text.replace(OLD_CLK, NEW_CLK).replace(OLD, NEW), encoding="utf-8")
