"""Fault: a measure without bounds (gates.yaml's ade spec_lint row)."""
from pathlib import Path

import yaml


def plant(ws: Path) -> None:
    p = ws / "spec" / "spec.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["measures"][0].pop("bounds", None)
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
