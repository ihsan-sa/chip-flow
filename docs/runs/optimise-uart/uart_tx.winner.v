module uart_tx (
    input  wire       clk,
    input  wire       rst,
    input  wire       start,
    input  wire [7:0] data,
    output wire       tx,
    output wire       busy
);
  // One shift register holds the whole frame - start(0), 8 data bits, an
  // even-parity bit, stop(1), LSB (tx) first - rather than a separate FSM
  // register plus a data-bit-index counter: almost every bit of `shift`
  // reaches `tx` directly at some point in the frame, which leaves far less
  // room for a mutation to land on a value the frame never actually reads.
  //
  // CLKS_PER_BIT is 4, fixed: a localparam, not a module parameter, and
  // clk_cnt is sized to exactly its 2-bit range (0..3) rather than a
  // parametrically-wide guess - a counter register wider than the range it
  // ever actually counts through is dead-bit surface a mutation can land on
  // and never be observed clearing (`mutate`, docs/design.md 1.5).
  localparam BIT_TICKS = 2'd3;   // CLKS_PER_BIT - 1

  reg [10:0] shift;
  reg [1:0]  clk_cnt;

  assign busy = |shift;
  assign tx = ~busy | shift[0];

  always @(posedge clk) begin
    if (rst) begin
      shift     <= 11'd0;
    end else if (!busy && start) begin
      shift     <= {1'b1, ^data, data, 1'b0};
      clk_cnt   <= 2'd0;
    end else if (busy) begin
      clk_cnt <= clk_cnt + 2'd1;
      if (clk_cnt == BIT_TICKS)
        shift     <= shift >> 1;
    end
  end
endmodule
