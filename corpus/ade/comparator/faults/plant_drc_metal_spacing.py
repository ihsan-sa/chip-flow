"""plant_drc_metal_spacing.py - ade/comparator `drc` fault, gates.yaml's own
"a planted spacing violation": the outn metal1 column moves to 0.45um from
the outp column, centre to centre, so outn's 0.30um column sits 0.10um
from outp's via pads, under metal1's 0.23um minimum spacing (M1.2a). Both
nets stay wired as before, so only the metal rules can see it.
"""
from __future__ import annotations

from pathlib import Path

OLD = '"outn": ntap_x + TAP_SIZE + 3.0}'
NEW = '"outn": ntap_x + TAP_SIZE + 2.45}'


def plant(ws) -> None:
    gen_path = Path(ws) / "layout" / "gen_comparator.py"
    text = gen_path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise RuntimeError(
            f"{gen_path}: the line this fault edits was not found once - "
            "gen_comparator.py's own source shape changed")
    gen_path.write_text(text.replace(OLD, NEW), encoding="utf-8")
