"""plant_pex_long_output_line.py - ade/comparator `pex_sim` fault, gates.yaml's
own "an output routed on a long minimum-width metal1 line": the outn pin
moves from its metal1 column to the far end of a 500um run of 0.23um
(M1.1's minimum width) metal1. DRC and LVS stay clean - the same net,
legally drawn - but the line hangs its capacitance and resistance on outn,
and the post-layout decision delay the pex bench measures there goes past
its bound.
"""
from __future__ import annotations

from pathlib import Path

OLD = '        labels.append((net, cx, (row_top + row_bot) / 2, L1LBL))\n'
NEW = '        y = (row_top + row_bot) / 2\n        if net == "outn":\n            layoutlib.rect(top, cx, y - 0.115, cx + 500.0, y + 0.115, L1)\n            layoutlib.rect(top, cx + 500.0, y - 0.5, cx + 501.0, y + 0.5, L1)\n            cx = cx + 500.5\n        labels.append((net, cx, y, L1LBL))\n'


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_comparator.py"
    text = gen_path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise RuntimeError(
            f"{gen_path}: the line this fault edits was not found once - "
            "gen_comparator.py's own source shape changed")
    gen_path.write_text(text.replace(OLD, NEW), encoding="utf-8")
