"""plant_pex_long_output_line.py - ade/comparator `pex_sim` fault, gates.yaml's
own "an output routed on a long minimum-width metal1 line": the outn pin
moves from its latch column to the far end of a 500um run of 0.23um
(M1.1's minimum width) metal1. The outn metal2 track reaches past the left
clk column and drops to that line through one more via1. DRC and LVS stay
clean - the same net, legally drawn - but the line hangs its capacitance
and resistance on outn, and the post-layout decision delay the pex bench
measures there goes past its bound.
"""
from __future__ import annotations

from pathlib import Path

# the outn label leaves the latch column ...
OLD_LBL = ('        labels.append((out_net, (dx0 + dx1) / 2,\n'
           '                       (mid_y["outn"] + mid_y["outp"]) / 2, L1LBL))\n')
NEW_LBL = ('        if out_net != "outn":\n'
           '            labels.append((out_net, (dx0 + dx1) / 2,\n'
           '                           (mid_y["outn"] + mid_y["outp"]) / 2, L1LBL))\n')
# ... for the far end of the long line, drawn once every clk column is known
OLD_TRK = '    # --- the metal2 tracks, each spanning its own net\'s landings only\n'
NEW_TRK = ('    line_y = mid_y["outn"]\n'
           '    line_x = round(-clk_x - 1.5, 3)\n'
           '    via_at("mid", "outn", line_x)\n'
           '    layoutlib.rect(top, line_x - 500.0, line_y - 0.115, line_x,\n'
           '                   line_y + 0.115, L1)\n'
           '    layoutlib.rect(top, line_x - 501.0, line_y - 0.5, line_x - 500.0,\n'
           '                   line_y + 0.5, L1)\n'
           '    labels.append(("outn", line_x - 500.5, line_y, L1LBL))\n'
           + OLD_TRK)


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_comparator.py"
    text = gen_path.read_text(encoding="utf-8")
    if text.count(OLD_LBL) != 1 or text.count(OLD_TRK) != 1:
        raise RuntimeError(
            f"{gen_path}: the line this fault edits was not found once - "
            "gen_comparator.py's own source shape changed")
    gen_path.write_text(text.replace(OLD_LBL, NEW_LBL).replace(OLD_TRK, NEW_TRK),
                        encoding="utf-8")
