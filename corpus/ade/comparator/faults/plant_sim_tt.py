"""Fault: W and L swapped on one input device (gates.yaml's sim_tt row,
"W and L swapped on a mirror's output device", in comparator form): xmip
becomes 0.28um wide and 4um long, so the inp side barely pulls dp down,
the latch tips the wrong way and the first decision comes out negative."""
from pathlib import Path

OLD = "xmip   dp   inp  tail vss nfet_03v3 w=w_in      l=l_min"
NEW = "xmip   dp   inp  tail vss nfet_03v3 w=l_min     l=w_in"


def plant(ws: Path) -> None:
    p = ws / "netlist" / "comparator.cir"
    text = p.read_text(encoding="utf-8")
    if OLD not in text:
        raise RuntimeError(f"{p}: xmip's device line not found as expected")
    p.write_text(text.replace(OLD, NEW), encoding="utf-8")
