"""Visible tests for counter8 (docs/design.md section 2: written before the
RTL, from the spec alone). Every input change and every read happens right
after a FallingEdge, never mixed with RisingEdge in the same test: driving
or sampling adjacent to a RisingEdge trigger raced the DUT's own posedge
update in this simulator while writing this corpus rung (a write scheduled
too close to the edge it was meant to affect could still be one clock late).
FallingEdge gives a full half-period of margin either side and was the fix.
Deliberately does NOT exercise a one-clock reset asserted mid-run - see
holdout/test_counter8_holdout.py, which does, and is what the `holdout`
gate's own corpus fault targets."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

CLK_PERIOD_NS = 10


async def _reset(dut, cycles=3):
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    for _ in range(cycles):
        await FallingEdge(dut.clk)
    dut.rst.value = 0


# req: REQ-WRAP
@cocotb.test()
async def test_wraps_from_255_to_0(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut)
    prev = int(dut.count.value)
    assert prev == 0
    for _ in range(300):
        await FallingEdge(dut.clk)
        cur = int(dut.count.value)
        assert cur == (prev + 1) % 256, f"expected {(prev + 1) % 256} got {cur}"
        prev = cur


# req: REQ-RESET
@cocotb.test()
async def test_reset_held_at_start_clears_count(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut, cycles=4)
    assert int(dut.count.value) == 0
    await FallingEdge(dut.clk)
    assert int(dut.count.value) == 1
