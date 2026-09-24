"""Fault: a combinational loop (gates.yaml's synth row) - `lp` depends on
its own value and feeds `tx`'s own combinational assignment, so it survives
yosys's dead-code elimination the same way corpus/vde/counter8/faults/
plant_synth.py's own `lp` does."""
from pathlib import Path

BUGGY = """\
module uart_tx (
    input  wire       clk,
    input  wire       rst,
    input  wire       start,
    input  wire [7:0] data,
    output reg        tx,
    output reg        busy
);
  localparam BIT_TICKS = 2'd3;
  localparam FRAME_BITS = 4'd11;

  reg [10:0] shift;
  reg [3:0]  bits_left;
  reg [1:0]  clk_cnt;
  wire       lp;
  assign lp = lp ^ busy;

  always @(posedge clk) begin
    if (rst) begin
      tx        <= 1'b1;
      busy      <= 1'b0;
      shift     <= 11'b1_1111_1111_1;
      bits_left <= 4'd0;
      clk_cnt   <= 2'd0;
    end else if (!busy) begin
      tx <= 1'b1;
      if (start) begin
        shift     <= {1'b1, ^data, data, 1'b0};
        bits_left <= FRAME_BITS;
        clk_cnt   <= 2'd0;
        busy      <= 1'b1;
      end
    end else begin
      tx <= shift[0] ^ lp;
      if (clk_cnt == BIT_TICKS) begin
        clk_cnt   <= 2'd0;
        shift     <= shift >> 1;
        bits_left <= bits_left - 4'd1;
        if (bits_left == 4'd1)
          busy <= 1'b0;
      end else begin
        clk_cnt <= clk_cnt + 2'd1;
      end
    end
  end
endmodule
"""


def plant(ws: Path) -> None:
    (ws / "rtl" / "uart_tx.v").write_text(BUGGY, encoding="utf-8")
