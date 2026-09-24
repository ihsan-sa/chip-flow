"""plant_lvs_swapped_net.py - ade/mirror `lvs` fault (docs/design.md 1.5,
"### M9."): swaps the IOUT and VSS text labels in layout/gen_mirror.py, so
the physically-VSS-connected net (both sources + the substrate tap) ends up
named "IOUT" and M2's actual drain net ends up named "VSS" - a real swapped
net, not a renamed one: the GDS geometry is untouched, only which pad each
label sits on changes, so magic's extraction reports the SAME two devices
on DIFFERENT named nets than the reference schematic expects, and netgen
reports a real topology mismatch against corpus/ade/mirror/layout_ref/
mirror.spice's own IREF/IOUT/VSS wiring.
"""
from __future__ import annotations

from pathlib import Path

OLD_IOUT = 'labels.append(("IOUT", MIRROR_DX + 0.87, 0.11, L1LBL))'
OLD_VSS = 'labels.append(("VSS", 2.0, -1.075, L1LBL))'
NEW_IOUT = 'labels.append(("VSS", MIRROR_DX + 0.87, 0.11, L1LBL))'
NEW_VSS = 'labels.append(("IOUT", 2.0, -1.075, L1LBL))'


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_mirror.py"
    text = gen_path.read_text(encoding="utf-8")
    if OLD_IOUT not in text or OLD_VSS not in text:
        raise RuntimeError(
            f"{gen_path}: IOUT/VSS label lines not found as expected - "
            "gen_mirror.py's own source shape changed")
    text = text.replace(OLD_IOUT, NEW_IOUT).replace(OLD_VSS, NEW_VSS)
    gen_path.write_text(text, encoding="utf-8")
