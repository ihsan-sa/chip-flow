"""plant_lvs_swapped_net.py - ade/mirror `lvs` fault (docs/design.md 1.5,
"### M9."): swaps the iout and vss labels in layout/gen_mirror.py, so the
net that really joins both sources and the substrate tap is named "iout"
and xmout's drain is named "vss". The geometry is untouched; only which
pad each label sits on changes, so magic extracts the same two devices on
differently named nets than netlist/mirror.cir wires them, and netgen
reports a real topology mismatch.
"""
from __future__ import annotations

from pathlib import Path

OLD_IOUT = 'labels.append(("iout", (d[0] + d[2]) / 2'
OLD_VSS = 'labels.append(("vss", (ref["s"][2]'
NEW_IOUT = 'labels.append(("vss", (d[0] + d[2]) / 2'
NEW_VSS = 'labels.append(("iout", (ref["s"][2]'


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_mirror.py"
    text = gen_path.read_text(encoding="utf-8")
    if OLD_IOUT not in text or OLD_VSS not in text:
        raise RuntimeError(
            f"{gen_path}: iout/vss label lines not found as expected - "
            "gen_mirror.py's own source shape changed")
    text = text.replace(OLD_IOUT, NEW_IOUT).replace(OLD_VSS, NEW_VSS)
    gen_path.write_text(text, encoding="utf-8")
