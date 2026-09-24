"""Fault: the digital side never acts on the analog waveform - here, by
tying the divider's clock input to a constant instead of the ring
oscillator's output. `cosim` must catch this as `digital_side_never_toggled`,
not as a bare test failure."""
from pathlib import Path

STUCK_TOP = """\
`timescale 1ns/1ps
module clk_div2 (
    input  wire osc_in,
    output reg  clk_div
);
  initial clk_div = 1'b0;
  always @(posedge osc_in)
    clk_div <= ~clk_div;
endmodule

module ring_osc_div_top (
    output wire osc_out,
    output wire clk_div
);
  // FAULT: osc_in tied to a constant - clk_div can never toggle, whatever
  // the analog side computes.
  clk_div2 divider (.osc_in(1'b0), .clk_div(clk_div));
endmodule
"""


def plant(ws: Path) -> None:
    path = ws / "tb" / "ring_osc_div_top.v"
    if not path.is_file():
        raise ValueError(f"{path}: not found")
    path.write_text(STUCK_TOP, encoding="utf-8")
