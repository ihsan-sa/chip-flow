"""Fault: SRA/SRAI shift logically (zero fill) instead of arithmetically -
invisible to tb/test_rv_core.py, which never shifts a negative operand
right arithmetically. Caught only by holdout/test_rv_core_holdout.py."""
from pathlib import Path

OLD = "wire [31:0] sra   = $signed(a) >>> alu_b[4:0];"
NEW = "wire [31:0] sra   = a >> alu_b[4:0];"


def plant(ws: Path) -> None:
    path = ws / "rtl" / "rv_core.v"
    text = path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise ValueError(f"{path}: expected exactly one {OLD!r}")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
