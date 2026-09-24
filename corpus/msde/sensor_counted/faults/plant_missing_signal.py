"""Fault: a crossing signal interface.yaml names that one side's spec
never declares (the splitter's two sides drifted)."""
from pathlib import Path

import yaml


def plant(ws: Path) -> None:
    path = ws / "digital_spec.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    before = data.get("interface") or []
    after = [sig for sig in before if sig.get("name") != "osc_en"]
    if len(after) == len(before):
        raise ValueError(f"{path}: no 'osc_en' entry to remove")
    data["interface"] = after
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
