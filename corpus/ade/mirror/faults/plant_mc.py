"""Fault: a window that typical meets but mismatch doesn't (gates.yaml's mc
row) - narrows iout_raw to [22.5, 25.5] uA (+-6% around typical) in both
spec.yaml and tb/mirror_tb.bounds.json. Typical (24.04 uA) still passes
sim_tt, but this design's own mismatch spread at seeds 1-20 (21.9 to 26.9
uA, sigma 1.39 uA, measured with the real PDK) puts 7 of the 20 samples
outside it: yield 65%, below spec.yaml's 95%. Only Monte Carlo sees it."""
import json
from pathlib import Path

LO, HI = 22.5e-6, 25.5e-6


def plant(ws: Path) -> None:
    p = ws / "tb" / "mirror_tb.bounds.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for entry in data:
        if entry["measure"] == "iout_raw":
            entry["min"], entry["max"] = LO, HI
    p.write_text(json.dumps(data), encoding="utf-8")
    s = ws / "spec" / "spec.yaml"
    text = s.read_text(encoding="utf-8")
    old = "bounds: {min: 18e-6, max: 32e-6}"
    assert old in text, "mirror spec.yaml's iout_raw bounds moved"
    s.write_text(text.replace(old, f"bounds: {{min: {LO:g}, max: {HI:g}}}"),
                 encoding="utf-8")
