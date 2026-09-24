"""Fault: the frame is one bit period too long (FRAME_BITS off by one), so
`busy` (and the serial line) run one extra bit-time past the real stop bit -
caught directly by the visible busy-timing test."""
from pathlib import Path

OLD = "localparam FRAME_BITS = 4'd11; // start + 8 data + parity + stop"
NEW = "localparam FRAME_BITS = 4'd12; // start + 8 data + parity + stop"


def plant(ws: Path) -> None:
    path = ws / "rtl" / "uart_tx.v"
    text = path.read_text(encoding="utf-8")
    if OLD not in text:
        raise ValueError(f"{path}: FRAME_BITS localparam not found")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
