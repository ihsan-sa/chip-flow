"""Fault: meets at typical, loses headroom at slow and hot (gates.yaml's
sim_pvt row) - tightens tb/mirror_tb.bounds.json around the real typical
value (2.40432e-05 A) narrowly enough that typical still passes but the
default corner set's own ss point (2.22374e-05 A, 125 C / -10% supply -
"slow and hot") falls outside it. A spec/bounds fault, not a device fault:
the design itself is unchanged, only how tightly its real PVT spread is
being asked to fit."""
import json
from pathlib import Path


def plant(ws: Path) -> None:
    p = ws / "tb" / "mirror_tb.bounds.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for entry in data:
        if entry["measure"] == "iout_raw":
            entry["min"], entry["max"] = 23.9e-6, 24.2e-6
    p.write_text(json.dumps(data), encoding="utf-8")
