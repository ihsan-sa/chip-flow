"""Fault: W and L swapped on a mirror's output device (gates.yaml's sim_tt
row) - confirmed empirically: this design's real iout_raw goes from
2.40432e-05 A (inside [18e-6, 32e-6]) to 1.05769e-07 A (nowhere close) once
xmout's w/l values trade places."""
from pathlib import Path


def plant(ws: Path) -> None:
    p = ws / "netlist" / "mirror.cir"
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace(
        "xmout iout      iref_node vss vss nfet_03v3 w=8e-6 l=5e-7",
        "xmout iout      iref_node vss vss nfet_03v3 w=5e-7 l=8e-6"),
        encoding="utf-8")
