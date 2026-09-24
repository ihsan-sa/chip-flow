module spi_fifo (
    input  wire       clk,
    input  wire       rst,
    input  wire       sclk,
    input  wire       mosi,
    input  wire       cs_n,
    input  wire       rd_en,
    output reg  [7:0] rd_data,
    output wire       empty,
    output wire       full
);
  // 4-entry byte FIFO. wr_ptr/rd_ptr carry one extra bit past the 2-bit
  // index (the classic empty-vs-full pointer trick): empty when the full
  // pointers are equal, full when the indices match but the extra bits
  // differ.
  reg [7:0] mem [0:3];
  reg [2:0] wr_ptr, rd_ptr;
  wire [1:0] wr_idx = wr_ptr[1:0];
  wire [1:0] rd_idx = rd_ptr[1:0];

  assign empty = (wr_ptr == rd_ptr);
  assign full  = (wr_ptr[1:0] == rd_ptr[1:0]) && (wr_ptr[2] != rd_ptr[2]);

  // SPI receive shift register, sampled entirely in the clk domain: sclk
  // and cs_n are assumed slow relative to clk (no cross-domain
  // synchronizer) - a simplification this corpus rung takes deliberately,
  // not a real SPI slave's clock-domain crossing. Only 7 bits of history
  // are ever kept - the completed byte is {shift[6:0], mosi}, so an 8th
  // (top) bit would be written every cycle and read never.
  reg [6:0] shift;
  reg [2:0] bit_cnt;
  reg       sclk_d;
  wire      sclk_rise = sclk && !sclk_d;

  always @(posedge clk) begin
    if (rst) begin
      wr_ptr  <= 3'd0;
      rd_ptr  <= 3'd0;
      shift   <= 7'd0;
      bit_cnt <= 3'd0;
      sclk_d  <= 1'b0;
      rd_data <= 8'd0;
    end else begin
      sclk_d <= sclk;

      if (cs_n) begin
        bit_cnt <= 3'd0;
      end else if (sclk_rise) begin
        shift <= {shift[5:0], mosi};
        if (bit_cnt == 3'd7) begin
          bit_cnt <= 3'd0;
          if (!full) begin
            mem[wr_idx] <= {shift[6:0], mosi};
            wr_ptr <= wr_ptr + 3'd1;
          end
        end else begin
          bit_cnt <= bit_cnt + 3'd1;
        end
      end

      if (rd_en && !empty) begin
        rd_data <= mem[rd_idx];
        rd_ptr  <= rd_ptr + 3'd1;
      end
    end
  end
endmodule
