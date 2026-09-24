"""Fault: counter wraps one early (gates.yaml's sim row) - wraps at 254 -> 0
instead of 255 -> 0, which tb/test_counter8.py's per-cycle wrap check
catches directly."""
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
    else if (count == 8'd254)
      count <= 8'd0;
    else
      count <= count + 8'd1;
  end
endmodule
"""


def plant(ws: Path) -> None:
    (ws / "rtl" / "counter8.v").write_text(BUGGY, encoding="utf-8")
