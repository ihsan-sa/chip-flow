"""Fault: a model not in the PDK (gates.yaml's netlist_lint row) - same
shape as corpus/ade/mirror's own plant_netlist_lint.py, on one of this
rung's own nfet devices."""
from pathlib import Path


def plant(ws: Path) -> None:
    p = ws / "netlist" / "bandgap.cir"
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace("nfet_03v3", "nfet_99v9"), encoding="utf-8")
