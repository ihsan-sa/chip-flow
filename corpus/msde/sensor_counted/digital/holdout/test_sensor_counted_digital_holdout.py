"""Held-out test for sensor_counted_digital (docs/design.md section 2). Not
part of tb/, never given to the rtl-writer or fixer agent's file list - only
run by the `holdout` gate. Exercises the one edge case the visible tests
(tb/test_sensor_counted_digital.py) never look at: an osc_out frequency fast
enough to produce more than 255 edges in one 1024-clk gate window, which
count must saturate at rather than wrap past (REQ-COUNT's own "saturating at
255 rather than wrapping" - tb/'s own two frequencies both stay well under
that ceiling on purpose). A design that lets count wrap (e.g. a plain 8-bit
modulo counter with no saturation clamp) passes every visible gate and is
only caught here.

Inputs follow the same discipline as tb/ - osc_out is driven by a free
running Timer loop, never tied to dut.clk."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

CLK_PERIOD_NS = 20
WINDOW_CYCLES = 1024


async def _reset(dut, cycles=3):
    dut.rst_n.value = 0
    dut.en.value = 0
    dut.osc_out.value = 0
    await FallingEdge(dut.clk)
    for _ in range(cycles):
        await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


async def _drive_osc(dut, period_ns):
    value = 0
    while True:
        await Timer(period_ns / 2, unit="ns")
        value ^= 1
        dut.osc_out.value = value


# req: REQ-COUNT
@cocotb.test()
async def test_count_saturates_rather_than_wraps(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut)
    # far faster than any real ring oscillator this block models (~1-12MHz)
    # but that realism is tb/'s job (REQ-COUNT's sim tests) - this test's
    # only job is to push the edge count well past 255 in one window
    # (1024 * 20ns / 4ns = 5120 possible edges) and confirm the clamp holds.
    osc_period_ns = 4
    cocotb.start_soon(_drive_osc(dut, osc_period_ns))
    dut.en.value = 1
    await RisingEdge(dut.valid)
    assert int(dut.count.value) == 255, (
        "count must saturate at 255, not wrap, when far more than 255 "
        "edges occur in one gate window")
