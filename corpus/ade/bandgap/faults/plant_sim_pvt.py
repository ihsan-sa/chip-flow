"""Fault: meets at typical, loses headroom at slow and hot (gates.yaml's
sim_pvt row) - tightens tb/bandgap_tb.bounds.json's `vref` bound narrowly
around the real typical value (1.18814V) so typical still passes but the
default corner set's own ss point (1.18271V, 125 C / -10% supply - "slow
and hot") and sf (1.18011V) fall outside it. A spec/bounds fault, not a
device fault: the design itself is unchanged, only how tightly its real
PVT spread is being asked to fit (same trick corpus/ade/mirror's own
plant_sim_pvt.py uses)."""
import json
from pathlib import Path


def plant(ws: Path) -> None:
    p = ws / "tb" / "bandgap_tb.bounds.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for entry in data:
        if entry["measure"] == "vref":
            entry["min"], entry["max"] = 1.186, 1.191
    p.write_text(json.dumps(data), encoding="utf-8")
