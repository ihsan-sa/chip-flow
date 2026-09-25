"""Held-out test for counter8 (docs/design.md section 2). Not part of tb/,
never given to the rtl-writer or fixer agent's file list - only run by the
`holdout` gate. Exercises the one edge case the visible tests
(tb/test_counter8.py) never look at: a reset asserted for exactly ONE clock
in the middle of a run, which must still clear count on that very clock -
the same requirement (REQ-RESET) the spec.md prose calls out ("including a
one-clock reset asserted mid-run"). tb/'s own reset test only ever holds rst
for several clocks at start-up, so a design whose reset takes an extra clock
to take effect (a register on the reset path itself) passes every visible
gate and is only caught here.

Inputs change right after a FallingEdge, same discipline as tb/ - see that
file's own note on why."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

CLK_PERIOD_NS = 10


# req: REQ-RESET
@cocotb.test()
async def test_one_clock_reset_mid_run_clears_immediately(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())

    # settle into a known, stable counting state first - an ample reset
    # hold, well clear of the one-clock transient this test targets.
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    for _ in range(4):
        await FallingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(4):
        await FallingEdge(dut.clk)
    assert int(dut.count.value) == 4

    # the case under test: exactly one clock of reset, mid-run.
    dut.rst.value = 1
    await FallingEdge(dut.clk)
    assert int(dut.count.value) == 0, (
        "a one-clock mid-run reset must clear count on that very clock")
    dut.rst.value = 0
