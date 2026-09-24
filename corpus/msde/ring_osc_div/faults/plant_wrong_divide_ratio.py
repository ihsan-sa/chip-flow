"""Fault: the digital side toggles, but at the wrong rate - a divide-by-4
in place of the spec'd divide-by-2, pushing divided_freq_hz outside its
bound (docs/design.md's `cosim` fault, "control word polarity inverted",
one instance of a wider class: the divider's own behavior silently
disagreeing with what the spec measures)."""
from pathlib import Path

WRONG_RATIO_TOP = """\
`timescale 1ns/1ps
module clk_div2 (
    input  wire osc_in,
    output reg  clk_div
);
  // FAULT: divide-by-4 instead of divide-by-2 - toggles clk_div only every
  // other rising edge of osc_in.
  reg toggle_gate;
  initial begin
    clk_div = 1'b0;
    toggle_gate = 1'b0;
  end
  always @(posedge osc_in) begin
    toggle_gate <= ~toggle_gate;
    if (toggle_gate)
      clk_div <= ~clk_div;
  end
endmodule

module ring_osc_div_top (
    output wire osc_out,
    output wire clk_div
);
  clk_div2 divider (.osc_in(osc_out), .clk_div(clk_div));
endmodule
"""


def plant(ws: Path) -> None:
    path = ws / "tb" / "ring_osc_div_top.v"
    if not path.is_file():
        raise ValueError(f"{path}: not found")
    path.write_text(WRONG_RATIO_TOP, encoding="utf-8")
