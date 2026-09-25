"""Fault: a model not in the PDK (gates.yaml's netlist_lint row) - every
nfet_03v3 in netlist/comparator.cir becomes nfet_99v9, a name no
gf180mcu_fd_pr model file declares; netlistlib.known_models()'s static
scan catches it before ngspice is asked to run."""
from pathlib import Path


def plant(ws: Path) -> None:
    p = ws / "netlist" / "comparator.cir"
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace("nfet_03v3", "nfet_99v9"), encoding="utf-8")
