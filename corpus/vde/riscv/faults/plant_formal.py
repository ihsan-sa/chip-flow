"""Fault: a register with no reset (gates.yaml's formal row) - rst no longer
returns the control state to idle, so a core halted before rst can still
read as halted after it. sim/holdout are not exercised by this manifest
entry - see corpus/vde/counter8/faults/plant_formal.py's own note."""
from pathlib import Path

OLD = "      state   <= S_IDLE;\n      pc      <= 5'd0;"
NEW = "      pc      <= 5'd0;"


def plant(ws: Path) -> None:
    path = ws / "rtl" / "rv_core.v"
    text = path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise ValueError(f"{path}: expected exactly one {OLD!r}")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
