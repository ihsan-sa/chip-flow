"""Fault: meets at typical, loses headroom at slow and hot (gates.yaml's
sim_pvt row) - tightens tdelay's bench bound to 0.40ns, above the real
typical 0.357ns but below the ss (125 C, -10% supply) 0.494ns and sf
0.474ns. The design is unchanged; only how tightly its real PVT spread is
asked to fit."""
import json
from pathlib import Path


def plant(ws: Path) -> None:
    p = ws / "tb" / "comparator_tb.bounds.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for entry in data:
        if entry["measure"] == "tdelay":
            entry["max"] = 4.0e-10
    p.write_text(json.dumps(data), encoding="utf-8")
