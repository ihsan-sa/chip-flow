// A spec-conforming uart_tx (corpus/vde/uart/spec.md) with ZERO start
// latency: the posedge that samples `start` also drives the start bit onto
// tx. The corpus reference puts it there one clock later. Both conform, so
// the corpus tests must pass both (tests/test_uart_holdout_latency.py).
module uart_tx (
    input  wire       clk,
    input  wire       rst,
    input  wire       start,
    input  wire [7:0] data,
    output reg        tx,
    output reg        busy
);
  reg [7:0] d;
  reg [3:0] idx;   // slot on tx: 0 start, 1..8 data, 9 parity, 10 stop
  reg [1:0] cnt;   // clocks into the slot, CLKS_PER_BIT = 4

  wire [3:0] nxt = idx + 4'd1;
  wire nxt_bit = (nxt <= 4'd8) ? d[nxt - 4'd1]
               : (nxt == 4'd9) ? ^d  // PARITY
               : 1'b1;

  always @(posedge clk) begin
    if (rst) begin
      tx <= 1'b1; busy <= 1'b0; d <= 8'd0; idx <= 4'd0; cnt <= 2'd0;
    end else if (!busy) begin
      tx <= 1'b1;
      if (start) begin
        d <= data; busy <= 1'b1; tx <= 1'b0; idx <= 4'd0; cnt <= 2'd0;
      end
    end else if (cnt == 2'd3) begin
      cnt <= 2'd0;
      if (idx == 4'd10) begin
        busy <= 1'b0; tx <= 1'b1;
      end else begin
        idx <= nxt; tx <= nxt_bit;
      end
    end else begin
      cnt <= cnt + 2'd1;
    end
  end
endmodule
