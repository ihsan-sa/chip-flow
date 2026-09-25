"""Fault: a combinational loop (gates.yaml's synth row) - `lp` depends on
its own value and feeds the debug port's output, so it survives yosys's
dead-code elimination the same way corpus/vde/counter8/faults/
plant_synth.py's own `lp` does."""
from pathlib import Path

OLD = "  assign dout = halted ? dbg_word[{din[1:0], 3'b000} +: 8] : 8'h00;\n"
NEW = """  wire lp;
  assign lp = lp ^ din[0];
  assign dout = halted ? dbg_word[{din[1:0], 3'b000} +: 8] ^ {7'd0, lp}
                       : 8'h00;
"""


def plant(ws: Path) -> None:
    path = ws / "rtl" / "rv_core.v"
    text = path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise ValueError(f"{path}: expected exactly one {OLD!r}")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
