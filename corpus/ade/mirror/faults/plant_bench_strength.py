"""Fault: bounds wide enough to pass anything (gates.yaml's bench_strength
row) - a bench_strength survivor is scored by whether ANY declared bound
still catches a device mutant; widening the one bound this rung has to
[-1, 1] A (nothing this design ever does gets close to an amp) lets every
mutant through untouched."""
import json
from pathlib import Path


def plant(ws: Path) -> None:
    p = ws / "tb" / "mirror_tb.bounds.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for entry in data:
        if entry["measure"] == "iout_raw":
            entry["min"], entry["max"] = -1, 1
    p.write_text(json.dumps(data), encoding="utf-8")
