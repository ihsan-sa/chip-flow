"""plant_pex_long_output_line.py - ade/mirror `pex_sim` fault, gates.yaml's
own: "an output routed on a long minimum-width metal1 line". The iout pin
moves from xmout's drain pad to the far end of a 500um run of 0.23um
(M1.1's minimum width) metal1. DRC and LVS stay clean - it is the same net,
legally drawn - but the line hangs its wire capacitance on the output and
magic's extresist puts its resistance in series, so the output pole the
pex bench measures falls well below its bound.
"""
from __future__ import annotations

from pathlib import Path

OLD = '''    d = out["d"]
    labels.append(("iout", (d[0] + d[2]) / 2, (d[1] + d[3]) / 2, L1LBL))
'''
NEW = '''    d = out["d"]
    y = (d[1] + d[3]) / 2
    layoutlib.rect(top, d[0], y - 0.115, d[2] + 500.0, y + 0.115, L1)
    layoutlib.rect(top, d[2] + 500.0, y - 0.5, d[2] + 501.0, y + 0.5, L1)
    labels.append(("iout", d[2] + 500.5, y, L1LBL))
'''


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_mirror.py"
    text = gen_path.read_text(encoding="utf-8")
    if OLD not in text:
        raise RuntimeError(
            f"{gen_path}: the iout label lines were not found as expected - "
            "gen_mirror.py's own source shape changed")
    gen_path.write_text(text.replace(OLD, NEW), encoding="utf-8")
