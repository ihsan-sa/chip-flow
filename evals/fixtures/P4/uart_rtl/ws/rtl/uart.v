// uart - 8E1 UART transmitter: start bit, data[0..7] LSB first, even parity,
// stop bit, each held CLKS_PER_BIT clocks. tx and busy are registered.
//
// A start accepted on edge N (idle, rst low) loads the frame; from edge N+1
// busy is 1 and tx carries the start bit. busy falls on the clock after the
// stop bit's hold completes (11 * CLKS_PER_BIT clocks of busy).

`default_nettype none

module uart #(
    parameter integer CLKS_PER_BIT = 4
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       start,
    input  wire [7:0] data,
    output reg        tx,
    output reg        busy
);

    // Hold counter width; at least 1 bit so CLKS_PER_BIT = 1 still elaborates.
    localparam integer CW = (CLKS_PER_BIT > 1) ? $clog2(CLKS_PER_BIT) : 1;
    localparam [31:0]   HOLD_LAST32 = CLKS_PER_BIT - 1;
    localparam [CW-1:0] HOLD_LAST   = HOLD_LAST32[CW-1:0];

    // Bits still to send after the one on tx: data[7:0], parity, stop (LSB first).
    reg [9:0]    shreg;
    // Bits left to send after the one currently on tx (10 at the start bit).
    reg [3:0]    bits_left;
    // Clocks the current bit has been held, 0..CLKS_PER_BIT-1.
    reg [CW-1:0] hold;

    always @(posedge clk) begin
        if (rst) begin
            tx   <= 1'b1;
            busy <= 1'b0;
        end else if (!busy) begin
            if (start) begin
                busy      <= 1'b1;
                tx        <= 1'b0;
                shreg     <= {1'b1, ^data, data};
                bits_left <= 4'd10;
                hold      <= {CW{1'b0}};
            end
        end else if (hold == HOLD_LAST) begin
            hold <= {CW{1'b0}};
            if (bits_left == 4'd0) begin
                // Stop bit's hold done: back to idle, line stays high.
                busy <= 1'b0;
                tx   <= 1'b1;
            end else begin
                tx        <= shreg[0];
                shreg     <= {1'b1, shreg[9:1]};
                bits_left <= bits_left - 4'd1;
            end
        end else begin
            hold <= hold + 1'b1;
        end
    end

endmodule

`default_nettype wire
