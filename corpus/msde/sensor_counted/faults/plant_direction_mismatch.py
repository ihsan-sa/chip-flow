"""Fault: a crossing signal whose direction disagrees between the two
specs (a polarity/direction mixup - the same class of bug gates.yaml names
for `cosim`, "control word polarity inverted", one stage earlier)."""
from pathlib import Path

import yaml


def plant(ws: Path) -> None:
    path = ws / "digital_spec.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for sig in data.get("interface") or []:
        if sig.get("name") == "osc_en":
            sig["direction"] = "a2d"
            break
    else:
        raise ValueError(f"{path}: no 'osc_en' entry to fault")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
