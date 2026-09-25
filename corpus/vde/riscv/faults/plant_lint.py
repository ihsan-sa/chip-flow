"""Fault: an always @* missing an else, i.e. a latch (gates.yaml's lint row)."""
from pathlib import Path

import faultlib


def plant(ws: Path) -> None:
    faultlib.append_latch_stub(ws / "rtl" / "rv_core.v")
