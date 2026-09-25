// sensor_counted_digital_formal.sv - the formal harness for
// corpus/msde/sensor_counted/digital (docs/design.md 1.5's `formal` gate,
// same convention corpus/vde/counter8/formal/counter8_formal.sv documents
// in full: a plain structural WRAPPER, never `bind` (yosys's `read
// -formal` frontend silently drops a `bind` statement), instantiating the
// DUT unmodified and forwarding clk/rst_n as ITS OWN top-level ports so
// rtl/sensor_counted_digital.v never carries a `` `ifdef FORMAL `` block of
// its own. Only procedural ("immediate") assert/cover inside an `always`
// block work under yosys's formal frontend - no SVA `assert property
// (@(posedge clk) ...)`.
//
// This wrapper proves only the two properties that hold regardless of the
// async osc_out input and its own oscillator frequency - the full 1024-
// cycle gate-window count (REQ-COUNT) is a sim/holdout/mutate/cover job,
// not a formal one (a k-induction proof at that depth is not what this
// gate is for).
module sensor_counted_digital_formal (
    input wire clk,
    input wire rst_n,
    input wire en,
    input wire osc_out,
    output wire osc_en,
    output wire [7:0] count,
    output wire valid
);
  sensor_counted_digital dut (
      .clk(clk),
      .rst_n(rst_n),
      .en(en),
      .osc_out(osc_out),
      .osc_en(osc_en),
      .count(count),
      .valid(valid)
  );

`ifdef FORMAL
  reg past_valid = 0;
  always @(posedge clk) past_valid <= 1;

  // REQ-FORMAL-RESET (spec.yaml): rst_n low forces count and valid to 0 on
  // the very next clock - proven for all time, not merely for whatever
  // resets tb/'s own tests happen to try.
  always @(posedge clk)
    if (past_valid)
      REQ_FORMAL_RESET: assert ($past(rst_n) || (count == 8'd0 && valid == 1'b0));

  // REQ-OSCEN (spec.yaml): osc_en tracks en combinationally, at all times.
  always @(posedge clk)
    REQ_FORMAL_ENABLE: assert (osc_en == en);

  // A cover point for REQ-VALID (spec.md): valid is actually reachable.
  always @(posedge clk)
    if (past_valid)
      COVER_VALID: cover (valid);
`endif
endmodule
