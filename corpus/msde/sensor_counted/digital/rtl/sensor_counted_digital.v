module sensor_counted_digital (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       en,
    input  wire       osc_out,
    output wire       osc_en,
    output reg  [7:0] count,
    output reg        valid
);
  localparam WINDOW = 1024;

  // osc_en = en, combinational (spec.md "Behaviour").
  assign osc_en = en;

  // 2-flop synchroniser bringing the asynchronous osc_out into the clk
  // domain, plus one more flop to edge-detect the synchronised signal.
  reg osc_sync0, osc_sync1, osc_sync1_d;
  always @(posedge clk) begin
    if (!rst_n) begin
      osc_sync0   <= 1'b0;
      osc_sync1   <= 1'b0;
      osc_sync1_d <= 1'b0;
    end else begin
      osc_sync0   <= osc_out;
      osc_sync1   <= osc_sync0;
      osc_sync1_d <= osc_sync1;
    end
  end

  wire osc_rise = osc_sync1 & ~osc_sync1_d;

  // Gate window: 1024 clk cycles. window_cnt holds at reset (0) whenever
  // en is low - counting only ever happens while en is high.
  reg [9:0] window_cnt;
  reg [7:0] edge_cnt;
  wire window_end = (window_cnt == WINDOW - 1);
  // this cycle's edge_cnt including any rising edge seen THIS cycle -
  // latched into count on the window-end cycle itself, so the edge that
  // completes the window is never dropped. Saturates at 255.
  wire [7:0] edge_cnt_next =
      (osc_rise && edge_cnt != 8'd255) ? edge_cnt + 8'd1 : edge_cnt;

  always @(posedge clk) begin
    if (!rst_n) begin
      window_cnt <= 10'd0;
      edge_cnt   <= 8'd0;
      count      <= 8'd0;
      valid      <= 1'b0;
    end else if (!en) begin
      window_cnt <= 10'd0;
      edge_cnt   <= 8'd0;
    end else if (window_end) begin
      window_cnt <= 10'd0;
      edge_cnt   <= 8'd0;
      count      <= edge_cnt_next;
      valid      <= 1'b1;
    end else begin
      window_cnt <= window_cnt + 10'd1;
      edge_cnt   <= edge_cnt_next;
    end
  end
endmodule
