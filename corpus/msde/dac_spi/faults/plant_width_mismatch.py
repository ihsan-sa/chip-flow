"""Fault: a control word width that differs between the two specs
(gates.yaml's named fault for `split`) - here the 8-bit DAC code declared
7 bits wide on the analog side."""
from pathlib import Path

import yaml


def plant(ws: Path) -> None:
    path = ws / "analog_spec.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for sig in data.get("interface") or []:
        if sig.get("name") == "code":
            sig["width"] = 7
            break
    else:
        raise ValueError(f"{path}: no 'code' entry to fault")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
