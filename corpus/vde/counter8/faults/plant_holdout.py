"""Fault: reset takes an extra clock to take effect (a register on the reset
path itself). Invisible to tb/test_counter8.py, whose only reset test holds
rst for several clocks at start-up - by the time it checks, the delay has
long since resolved. Caught only by holdout/test_counter8_holdout.py's
one-clock mid-run reset (gates.yaml's holdout row: "... which the visible
tests do not look")."""
from pathlib import Path

BUGGY = """\
module counter8 (
    input  wire       clk,
    input  wire       rst,
    output reg  [7:0] count
);
  reg rst_d;
  always @(posedge clk) begin
    rst_d <= rst;
    count <= rst_d ? 8'd0 : count + 8'd1;
  end
endmodule
"""


def plant(ws: Path) -> None:
    (ws / "rtl" / "counter8.v").write_text(BUGGY, encoding="utf-8")
