"""Fault: a control word width that differs between the two specs
(gates.yaml's named fault for `split`)."""
from pathlib import Path

import yaml


def plant(ws: Path) -> None:
    path = ws / "analog_spec.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for sig in data.get("interface") or []:
        if sig.get("name") == "osc_en":
            sig["width"] = 2
            break
    else:
        raise ValueError(f"{path}: no 'osc_en' entry to fault")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
