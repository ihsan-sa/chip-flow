"""Fault: an unreachable state (gates.yaml's cover row) - an extra branch
guarded by a self-contradiction (`rst && !rst`) that tb/'s own tests, or any
other input sequence, can never reach."""
from pathlib import Path

BUGGY = """\
module counter8 (
    input  wire       clk,
    input  wire       rst,
    output reg  [7:0] count
);
  always @(posedge clk) begin
    if (rst)
      count <= 8'd0;
    else if (rst && !rst)
      count <= 8'hAA;
    else
      count <= count + 8'd1;
  end
endmodule
"""


def plant(ws: Path) -> None:
    (ws / "rtl" / "counter8.v").write_text(BUGGY, encoding="utf-8")
