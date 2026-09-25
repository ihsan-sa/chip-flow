"""Fault: W and L swapped on the output branch's own mirror device
(gates.yaml's sim_tt row, same class of fault as corpus/ade/mirror's own
plant_sim_tt.py) - confirmed empirically: this design's real `vref` goes
from 1.18814V (inside spec.yaml's [1.16, 1.21] bound) to 0.716937V (nowhere
close) once xm5's own w/l values trade places."""
from pathlib import Path


def plant(ws: Path) -> None:
    p = ws / "netlist" / "bandgap.cir"
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace(
        "xm5  vref pb vdd vdd pfet_03v3 w=10e-6 l=4e-6",
        "xm5  vref pb vdd vdd pfet_03v3 w=4e-6 l=10e-6"),
        encoding="utf-8")
