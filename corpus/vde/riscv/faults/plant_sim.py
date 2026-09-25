"""Fault: a taken branch lands one instruction past its target - the
visible loop and branch tests catch it directly."""
from pathlib import Path

OLD = "OP_BRANCH: pc <= take ? pc + off_b : pc_next[4:0];"
NEW = "OP_BRANCH: pc <= take ? pc + off_b + 5'd1 : pc_next[4:0];"


def plant(ws: Path) -> None:
    path = ws / "rtl" / "rv_core.v"
    text = path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise ValueError(f"{path}: expected exactly one {OLD!r}")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
