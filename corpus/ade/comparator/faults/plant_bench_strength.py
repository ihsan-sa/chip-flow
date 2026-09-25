"""Fault: bounds wide enough to pass anything (gates.yaml's bench_strength
row) - every bound in tb/comparator_tb.bounds.json opens to a range no
output of this 3.3V design can leave, so no device mutant is caught."""
import json
from pathlib import Path

WIDE = {"vdiff_pos": {"min": -10.0}, "vdiff_neg": {"max": 10.0},
        "tdelay": {"min": 0.0, "max": 1.0}, "vreset": {"min": -10.0},
        "itail_peak": {"min": -1.0, "max": 1.0}}


def plant(ws: Path) -> None:
    p = ws / "tb" / "comparator_tb.bounds.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for entry in data:
        entry.pop("min", None)
        entry.pop("max", None)
        entry.update(WIDE[entry["measure"]])
    p.write_text(json.dumps(data), encoding="utf-8")
