// tt_um_macro_spike.v - the digital side of the macro_harden spike: a
// 4-bit counter whose bit 0 drives the analog inverter macro's input, and
// a flop that samples the macro's output. uo_out[0] is that sample,
// uo_out[4:1] the counter.
//
// With `define MACRO_IN_UNCONNECTED the macro's input is left open instead
// (the negative LVS case in run.sh section c).
`default_nettype none

module tt_um_macro_spike (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  reg  [3:0] cnt;
  reg        inv_q;
  wire       inv_out;

  always @(posedge clk) begin
    if (!rst_n) begin
      cnt   <= 4'd0;
      inv_q <= 1'b0;
    end else begin
      cnt   <= cnt + 4'd1;
      inv_q <= inv_out;
    end
  end

  inv_macro u_inv (
`ifdef MACRO_IN_UNCONNECTED
      .in (),
`else
      .in (cnt[0]),
`endif
      .out(inv_out)
  );

  assign uo_out  = {3'b000, cnt, inv_q};
  assign uio_out = 8'h00;
  assign uio_oe  = 8'h00;
  wire _unused = &{ena, ui_in, uio_in, 1'b0};
endmodule

`default_nettype wire
