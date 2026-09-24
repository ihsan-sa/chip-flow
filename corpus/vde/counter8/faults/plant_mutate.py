"""Fault: a testbench that asserts nothing (gates.yaml's mutate row)."""
from pathlib import Path

import faultlib


def plant(ws: Path) -> None:
    faultlib.weaken_all_tests(ws / "tb")
