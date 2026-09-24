"""Fault: reset must be held for TWO consecutive clocks to take effect (a
register on the reset path itself, gated with the raw signal rather than
replacing it - `rst && rst_d`, not `rst_d` alone). Invisible to
tb/test_counter8.py, whose only reset test holds rst for several (3-4)
clocks at start-up - long enough for the two-clock requirement to be met
well before the test ever checks. Caught only by
holdout/test_counter8_holdout.py's one-clock mid-run reset (gates.yaml's
holdout row: "... which the visible tests do not look"): one clock is never
enough to clear count under this fault, so that test alone fails.

An earlier version of this fault gated on `rst_d` alone (dropping `rst`
entirely), which delays reset by one clock rather than requiring two held -
that also broke tb/'s own 3-4-clock reset tests (a one-clock delay is still
visible at 3-4 clocks), so the fault never demonstrated holdout catching
anything tb/ missed. faults.py's own harness (run_rung) now asserts the sim
gate still passes for every holdout fault, which would have caught that."""
from pathlib import Path

BUGGY = """\
module counter8 (
    input  wire       clk,
    input  wire       rst,
    output reg  [7:0] count
);
  reg rst_d;
  always @(posedge clk) begin
    rst_d <= rst;
    count <= (rst && rst_d) ? 8'd0 : count + 8'd1;
  end
endmodule
"""


def plant(ws: Path) -> None:
    (ws / "rtl" / "counter8.v").write_text(BUGGY, encoding="utf-8")
