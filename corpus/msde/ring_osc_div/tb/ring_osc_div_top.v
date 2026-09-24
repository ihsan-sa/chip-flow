`timescale 1ns/1ps
// The digital side of the ring_osc_div cosim bench (docs/design.md 1.5
// msde table's `cosim` row): a real divide-by-2 flip-flop, compiled by
// Icarus like any other design. `osc_out` is a bare net cocotbext-ams's
// MixedSignalBridge forces from the ngspice-computed ring5 waveform (see
// test_ring_osc_div.py) - clk_div2 reacts to those forced edges exactly as
// it would to a real clock pin, which is the whole point of this gate: the
// digital side acts on the analog waveform, not a stub value.
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
  clk_div2 divider (.osc_in(osc_out), .clk_div(clk_div));
endmodule
