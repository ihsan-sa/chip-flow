// rv_core_formal.sv - the formal harness for corpus/vde/riscv (docs/
// design.md 1.5's `formal` gate, "### M3."). Same wrapper shape as
// corpus/vde/counter8/formal/counter8_formal.sv - see that file's own
// header for why this is a plain structural instantiation, never `bind`,
// and why the properties are procedural asserts/covers, never SVA
// `assert property`. Every input is left free, so the solver drives the
// loader, run and the debug select however it likes, and the register file
// and memory start from arbitrary values.
module rv_core_formal (
    input  wire       clk,
    input  wire       rst,
    input  wire       we,
    input  wire       run,
    input  wire [7:0] din,
    output wire [7:0] dout,
    output wire       halted,
    output wire       illegal
);
  rv_core dut (.clk(clk), .rst(rst), .we(we), .run(run), .din(din),
               .dout(dout), .halted(halted), .illegal(illegal));

`ifdef FORMAL
  reg past_valid = 0;
  always @(posedge clk) past_valid <= 1;

  // REQ-FORMAL-RESET (spec.yaml): a held or pulsed rst leaves the core
  // idle - not halted, no illegal flag, dout 0 - on the very next clock.
  always @(posedge clk)
    if (past_valid)
      REQ_FORMAL_RESET: assert (!$past(rst) ||
                                (!halted && !illegal && dout == 8'h00));

  // REQ-FORMAL-X0 (spec.yaml): the debug port never shows x0 as anything
  // but 0, whatever the program wrote to it.
  always @(*)
    REQ_FORMAL_X0: assert (!(halted && !din[7] && din[5:2] == 4'd0) ||
                           dout == 8'h00);

  // A cover point for REQ-HALT: after a reset, the core runs and stops on
  // ecall/ebreak rather than on an illegal word.
  reg reset_seen = 0;
  always @(posedge clk) if (rst) reset_seen <= 1;
  always @(posedge clk)
    COVER_HALTED_CLEANLY: cover (reset_seen && halted && !illegal);
`endif
endmodule
