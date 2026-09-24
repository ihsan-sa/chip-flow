// counter8_formal.sv - the formal harness for corpus/vde/counter8 (docs/
// design.md 1.5's `formal` gate, "### M3.").
//
// A plain structural WRAPPER, never `bind`: yosys's `read -formal` frontend
// silently drops a `bind` statement (proved empirically while building
// engine/scripts/check_formal.py - the design "proves" vacuously, with
// zero properties in sby's own model, when the checker is attached that
// way). This module instantiates the DUT unmodified and forwards clk/rst
// as ITS OWN top-level ports (never internally driven - sby's own
// clk2fflogic step needs clk/rst to stay free top-level inputs, not a
// testbench-style clock generator) so rtl/counter8.v itself never has to
// carry a `` `ifdef FORMAL `` block of its own.
//
// yosys's formal frontend has no SVA `assert property (@(posedge clk) ...)`
// support either (also proved empirically - a syntax error at the `@`).
// Only PROCEDURAL ("immediate") assert/cover statements inside an `always`
// block work, guarded the same way tests/check.sh's own sby smoke does
// (past_valid, so the very first cycle - whose $past is undefined - never
// fires a spurious counterexample).
module counter8_formal (input wire clk, input wire rst, output wire [7:0] count);
  counter8 dut (.clk(clk), .rst(rst), .count(count));

`ifdef FORMAL
  reg past_valid = 0;
  always @(posedge clk) past_valid <= 1;

  // REQ-FORMAL-RESET (spec.yaml): a held/pulsed rst forces count to 0 on
  // the very next clock - proven for all time, not merely for the specific
  // resets tb/'s own tests happen to try.
  always @(posedge clk)
    if (past_valid)
      REQ_FORMAL_RESET: assert (!$past(rst) || count == 8'd0);

  // A cover point for the state REQ-WRAP (spec.md) names: the counter
  // actually reaches 255 before it wraps back to 0.
  always @(posedge clk)
    COVER_WRAP: cover (count == 8'hFF);
`endif
endmodule
