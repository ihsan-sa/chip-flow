// uart_formal - formal harness for `uart`, written from spec/spec.md and
// spec/spec.yaml before any RTL exists.
//
// Plain wrapper (no bind): instantiates the DUT and forwards its ports.
// clk/rst/start/data are the wrapper's own free top-level inputs.
//
// REQ-START-WHILE-BUSY is checked as non-interference against a second,
// shadow copy of the same DUT (u_ref) whose start is masked while its own
// busy is 1, i.e. a copy that never sees a start while busy. If the DUT
// really ignores such a start (no disturbance, nothing queued), the two
// copies stay identical on tx and busy for all time.

module uart_formal #(
    parameter CLKS_PER_BIT = 4
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       start,
    input  wire [7:0] data,
    output wire       tx,
    output wire       busy
);

    uart #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_dut (
        .clk   (clk),
        .rst   (rst),
        .start (start),
        .data  (data),
        .tx    (tx),
        .busy  (busy)
    );

    // Shadow copy: identical inputs, except start is withheld whenever the
    // shadow is busy.
    wire ref_tx;
    wire ref_busy;
    wire ref_start = start & ~ref_busy;

    uart #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_ref (
        .clk   (clk),
        .rst   (rst),
        .start (ref_start),
        .data  (data),
        .tx    (ref_tx),
        .busy  (ref_busy)
    );

`ifdef FORMAL
    reg past_valid = 1'b0;
    always @(posedge clk)
        past_valid <= 1'b1;

    // Start from a known state: rst is sampled high on the first clock.
    always @(*)
        if (!past_valid)
            assume (rst);

    always @(posedge clk) begin
        if (past_valid) begin
            // REQ-RESET: rst sampled high on an edge -> idle on the next
            // clock (tx 1, busy 0), whatever was in flight.
            if ($past(rst))
                p_reset_idle: assert (tx == 1'b1 && busy == 1'b0);

            // REQ-TX-IDLE-HIGH-WHEN-NOT-BUSY: outside a frame the line is 1.
            if (!busy)
                p_tx_high_when_not_busy: assert (tx == 1'b1);

            // REQ-START-WHILE-BUSY: a start while busy changes nothing -
            // the DUT stays cycle-identical to the copy that never saw it.
            p_start_ignored_while_busy: assert (tx == ref_tx && busy == ref_busy);
        end
    end

    // Cover points: states the spec names as reachable (all within the
    // default depth of 20; a complete 44-clock frame is not - see OPEN).
    always @(posedge clk) begin
        if (past_valid) begin
            // A frame begins: busy rises and the start bit is on the line.
            COVER_FRAME_START: cover (!$past(busy) && busy && tx == 1'b0);

            // A start pulse arrives while a frame is in flight.
            COVER_START_WHILE_BUSY: cover (!$past(rst) && $past(busy) && $past(start) && busy);

            // rst lands mid-frame and abandons it.
            COVER_RESET_MID_FRAME: cover ($past(rst) && $past(busy) && !busy);

            // The line goes from the start bit to a 1 data bit mid-frame.
            COVER_DATA_BIT_HIGH: cover ($past(busy) && busy && !$past(tx) && tx);
        end
    end
`endif

endmodule
