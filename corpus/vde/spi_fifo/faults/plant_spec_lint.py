"""Fault: a requirement with no way to check it (gates.yaml's spec_lint row)."""
from pathlib import Path

import faultlib


def plant(ws: Path) -> None:
    faultlib.strip_requirement_check(ws / "spec" / "spec.yaml", "REQ-RX")
