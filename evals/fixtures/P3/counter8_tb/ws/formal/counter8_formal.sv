// counter8_formal - structural formal wrapper for counter8 (written from
// spec/spec.md + spec/spec.yaml before any RTL exists).
//
// Instantiates the DUT directly (never `bind`) and carries procedural
// assert/cover statements only (yosys's formal frontend has no SVA
// `assert property`). clk/rst are this wrapper's own free top-level ports.

module counter8_formal (
    input  wire       clk,
    input  wire       rst,
    output wire [7:0] count
);

    counter8 dut (
        .clk   (clk),
        .rst   (rst),
        .count (count)
    );

`ifdef FORMAL
    // past_valid: $past() is undefined on the first cycle; hold every check
    // off until at least one clock edge of history exists.
    reg past_valid = 1'b0;
    // past_valid2/3: two/three edges of history, for the multi-cycle covers.
    reg past_valid2 = 1'b0;
    reg past_valid3 = 1'b0;
    always @(posedge clk) begin
        past_valid  <= 1'b1;
        past_valid2 <= past_valid;
        past_valid3 <= past_valid2;
    end

    always @(posedge clk) begin
        if (past_valid) begin
            // REQ-INC: rst low at an edge -> count is previous + 1 (mod 256).
            if (!$past(rst))
                p_inc: assert (count == $past(count) + 8'd1);

            // REQ-RST-SYNC: rst high at an edge -> count is 0 after that edge.
            if ($past(rst))
                p_rst_sync: assert (count == 8'd0);
        end
    end

    // Cover points: states the spec names as reachable.
    always @(posedge clk) begin
        if (past_valid) begin
            // REQ-WRAP: 255 with rst low goes to 0.
            COVER_WRAP: cover (!$past(rst) && $past(count) == 8'd255
                               && count == 8'd0);
            // The top of the range is reachable at all.
            COVER_COUNT_MAX: cover (count == 8'd255);
            // Reset taking effect while the counter was mid-run (non-zero).
            COVER_RST_MIDRUN: cover ($past(rst) && $past(count) != 8'd0
                                     && count == 8'd0);
        end
        if (past_valid2) begin
            // Counting resumes at 1 on the edge after a reset is released.
            COVER_RESUME_AFTER_RST: cover ($past(rst, 2) && !$past(rst)
                                           && count == 8'd1);
        end
        if (past_valid3) begin
            // REQ-RST-PULSE: rst high for exactly one edge while count is
            // non-zero -> 0 on that edge, then 1 on the edge after release.
            COVER_RST_PULSE: cover (!$past(rst, 3) && $past(rst, 2)
                                    && !$past(rst)
                                    && $past(count, 2) != 8'd0
                                    && $past(count) == 8'd0
                                    && count == 8'd1);
        end
    end
`endif

endmodule
