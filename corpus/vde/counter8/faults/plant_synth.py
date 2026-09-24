"""Fault: a combinational loop (gates.yaml's synth row) - `lp` depends on
its own value and feeds `count`'s own next-state calculation, so it
survives yosys's dead-code elimination (an unread loop wire would be
optimized away before the loop check ever sees it - proved empirically
while building check_synth.py)."""
from pathlib import Path

BUGGY = """\
module counter8 (
    input  wire       clk,
    input  wire       rst,
    output reg  [7:0] count
);
  wire lp;
  assign lp = lp ^ count[0];
  always @(posedge clk) begin
    if (rst)
      count <= 8'd0;
    else
      count <= (count + 8'd1) ^ {7'd0, lp};
  end
endmodule
"""


def plant(ws: Path) -> None:
    (ws / "rtl" / "counter8.v").write_text(BUGGY, encoding="utf-8")
