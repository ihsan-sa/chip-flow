"""Fault: parity is computed inverted (odd instead of even) - invisible to
tb/test_uart_tx.py, which deliberately never checks the parity bit's value.
Caught only by holdout/test_uart_tx_holdout.py (gates.yaml's holdout row:
"UART parity inverted where the visible tests do not look")."""
from pathlib import Path

OLD = "shift     <= {1'b1, ^data, data, 1'b0};"
NEW = "shift     <= {1'b1, ~^data, data, 1'b0};"


def plant(ws: Path) -> None:
    path = ws / "rtl" / "uart_tx.v"
    text = path.read_text(encoding="utf-8")
    if OLD not in text:
        raise ValueError(f"{path}: parity assignment not found")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
