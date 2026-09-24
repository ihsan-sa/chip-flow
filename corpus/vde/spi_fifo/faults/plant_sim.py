"""Fault: the full/empty pointer-wrap comparison is inverted, so `full` and
`empty` come out wrong the moment the write pointer has wrapped once -
caught directly by the visible full/empty checks."""
from pathlib import Path

OLD = "  assign full  = (wr_ptr[1:0] == rd_ptr[1:0]) && (wr_ptr[2] != rd_ptr[2]);"
NEW = "  assign full  = (wr_ptr[1:0] == rd_ptr[1:0]) && (wr_ptr[2] == rd_ptr[2]);"


def plant(ws: Path) -> None:
    path = ws / "rtl" / "spi_fifo.v"
    text = path.read_text(encoding="utf-8")
    if OLD not in text:
        raise ValueError(f"{path}: expected full/empty comparison line not found")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
