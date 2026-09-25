"""Fault: a crossing signal whose clock domain disagrees between the two
specs - here the sample-and-hold control declared in a free-running domain
on the analog side instead of the digital side's system clock domain
(check_split.py emits its own `domain_mismatch` kind for this, distinct
from `width_mismatch` and `direction_mismatch`)."""
from pathlib import Path

import yaml


def plant(ws: Path) -> None:
    path = ws / "analog_spec.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for sig in data.get("interface") or []:
        if sig.get("name") == "sample":
            sig["domain"] = "clk_free"
            break
    else:
        raise ValueError(f"{path}: no 'sample' entry to fault")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
