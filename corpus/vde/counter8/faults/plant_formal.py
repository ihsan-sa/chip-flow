"""Fault: a register with no reset (gates.yaml's formal row) - `count`'s
reset branch is removed entirely. `sim`/`holdout` are not exercised by this
manifest entry (docs/design.md 1.5's formal fault: "sim passes, formal
fails" is the real-world reading, not something faults.py's own per-entry
contract re-checks here - see check_formal.py's own header for the reasons
the pass criteria are enforced the way they are)."""
from pathlib import Path

BUGGY = """\
module counter8 (
    input  wire       clk,
    input  wire       rst,
    output reg  [7:0] count
);
  always @(posedge clk) begin
    count <= count + 8'd1;
  end
endmodule
"""


def plant(ws: Path) -> None:
    (ws / "rtl" / "counter8.v").write_text(BUGGY, encoding="utf-8")
