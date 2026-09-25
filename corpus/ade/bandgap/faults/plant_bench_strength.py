"""Fault: bounds wide enough to pass anything (gates.yaml's bench_strength
row) - a bench_strength survivor is scored by whether ANY declared bound
still catches a device mutant; widening every bound this rung has, across
both benches, to values nothing this design ever produces (vref/
vref_line_pp/vref_tc_pp are all well under 3.3V - the supply rail) lets
every mutant of every declared device (xm1-5, xr1, xr2, xq1-3) through
untouched."""
import json
from pathlib import Path


def _blow_open(p: Path, measure: str) -> None:
    data = json.loads(p.read_text(encoding="utf-8"))
    for entry in data:
        if entry["measure"] == measure:
            entry["min"], entry["max"] = -10, 10
    p.write_text(json.dumps(data), encoding="utf-8")


def plant(ws: Path) -> None:
    _blow_open(ws / "tb" / "bandgap_tb.bounds.json", "vref")
    _blow_open(ws / "tb" / "bandgap_tb.bounds.json", "vref_line_pp")
    _blow_open(ws / "tb" / "bandgap_tc_tb.bounds.json", "vref_tc_pp")
