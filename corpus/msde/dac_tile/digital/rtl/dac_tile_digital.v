module dac_tile_digital (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [1:0] code_in,
    output wire       bmsb,
    output wire       blsb
);
  // 2-bit code register (spec.md "Behaviour"): loaded from code_in on every
  // clock, cleared by a synchronous active-low reset.
  reg [1:0] code;
  always @(posedge clk) begin
    if (!rst_n)
      code <= 2'b00;
    else
      code <= code_in;
  end

  assign bmsb = code[1];
  assign blsb = code[0];
endmodule
