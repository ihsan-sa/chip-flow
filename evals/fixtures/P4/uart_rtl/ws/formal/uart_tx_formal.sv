// uart_tx_formal.sv - the formal harness for corpus/vde/uart (docs/
// design.md 1.5's `formal` gate, "### M3."). Same wrapper shape as
// corpus/vde/counter8/formal/counter8_formal.sv - see that file's own
// header for why this is a plain structural instantiation, never `bind`,
// and why the properties are procedural asserts/covers, never SVA
// `assert property`.
module uart_tx_formal (
    input  wire       clk,
    input  wire       rst,
    input  wire       start,
    input  wire [7:0] data,
    output wire       tx,
    output wire       busy
);
  uart_tx dut (.clk(clk), .rst(rst), .start(start), .data(data),
              .tx(tx), .busy(busy));

`ifdef FORMAL
  reg past_valid = 0;
  always @(posedge clk) past_valid <= 1;

  // REQ-FORMAL-RESET (spec.yaml): a held/pulsed rst forces the idle line
  // state (tx=1, busy=0) on the very next clock - proven for all time.
  always @(posedge clk)
    if (past_valid)
      REQ_FORMAL_RESET: assert (!$past(rst) || (tx == 1'b1 && busy == 1'b0));

  // A cover point for the state REQ-BUSY (spec.md) names: a frame actually
  // starts (busy asserted).
  always @(posedge clk)
    COVER_FRAME_STARTED: cover (busy);
`endif
endmodule
