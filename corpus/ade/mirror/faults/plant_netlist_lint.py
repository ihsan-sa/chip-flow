"""Fault: a model not in the PDK (gates.yaml's netlist_lint row) - confirmed
empirically while building check_netlist_lint.py: a bad model name is one
of the few things this box's real ngspice actually exits nonzero AND prints
an "Error:" line for, but netlistlib.known_models()'s static scan catches
it before ngspice is even asked to run."""
from pathlib import Path


def plant(ws: Path) -> None:
    p = ws / "netlist" / "mirror.cir"
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace("nfet_03v3", "nfet_99v9"), encoding="utf-8")
