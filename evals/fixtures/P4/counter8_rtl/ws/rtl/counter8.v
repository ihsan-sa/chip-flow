// counter8 - 8-bit free-running binary counter with synchronous,
// active-high reset. Wraps 255 -> 0; no overflow output.
`default_nettype none

module counter8 (
    input  wire       clk,
    input  wire       rst,
    output reg  [7:0] count
);

    // REQ-RST-SYNC / REQ-RST-PULSE: rst sampled only at the rising edge.
    // REQ-INC / REQ-WRAP: +1 modulo 256 (8-bit add drops the carry).
    always @(posedge clk) begin
        if (rst)
            count <= 8'd0;
        else
            count <= count + 8'd1;
    end

endmodule

`default_nettype wire
