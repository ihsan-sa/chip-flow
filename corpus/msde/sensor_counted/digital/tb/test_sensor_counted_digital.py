"""Visible tests for sensor_counted_digital (docs/design.md section 2:
written before the RTL, from the spec alone). Drives osc_out as a free-
running async square wave via a Timer coroutine, never tied to clk edges,
at a couple of frequencies whose period is not a multiple of clk's 20ns -
the whole point of the 2-flop synchroniser this design carries. Every
osc_out frequency used here keeps the expected per-window edge count well
under the 255 saturation ceiling (REQ-COUNT's saturation behaviour is
deliberately left for holdout/test_sensor_counted_digital_holdout.py to
catch alone)."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer

CLK_PERIOD_NS = 20
WINDOW_CYCLES = 1024
WINDOW_NS = WINDOW_CYCLES * CLK_PERIOD_NS


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
    """Free-running async square wave on osc_out - a plain Timer loop, never
    triggered off dut.clk, so it drifts freely against the clk domain."""
    value = 0
    while True:
        await Timer(period_ns / 2, unit="ns")
        value ^= 1
        dut.osc_out.value = value


def _expected_edges(osc_period_ns):
    return WINDOW_NS / osc_period_ns


# req: REQ-COUNT
# req: REQ-OSCEN
@cocotb.test()
async def test_edge_count_at_first_frequency(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut)
    osc_period_ns = 137  # ~7.3 MHz, not a multiple of 20ns
    cocotb.start_soon(_drive_osc(dut, osc_period_ns))
    dut.en.value = 1
    await Timer(1, unit="ns")
    assert int(dut.osc_en.value) == 1
    await RisingEdge(dut.valid)
    expected = _expected_edges(osc_period_ns)
    got = int(dut.count.value)
    assert abs(got - expected) <= 2, f"expected ~{expected:.1f} got {got}"


# req: REQ-COUNT
@cocotb.test()
async def test_edge_count_at_second_frequency(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut)
    osc_period_ns = 1000  # 1 MHz, also not a multiple of 20ns
    cocotb.start_soon(_drive_osc(dut, osc_period_ns))
    dut.en.value = 1
    await RisingEdge(dut.valid)
    expected = _expected_edges(osc_period_ns)
    got = int(dut.count.value)
    assert abs(got - expected) <= 2, f"expected ~{expected:.1f} got {got}"


# req: REQ-HOLD
# req: REQ-OSCEN
@cocotb.test()
async def test_disable_holds_window_at_reset(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut)
    osc_period_ns = 137
    cocotb.start_soon(_drive_osc(dut, osc_period_ns))
    dut.en.value = 1
    await ClockCycles(dut.clk, 200)
    dut.en.value = 0
    await Timer(1, unit="ns")
    assert int(dut.osc_en.value) == 0
    # more than a full window's worth of cycles while disabled - a window
    # must never complete
    await ClockCycles(dut.clk, WINDOW_CYCLES + 50)
    assert int(dut.valid.value) == 0, "window must not complete while en is low"
    # re-enable: the window must be a fresh, full one, not the partial one
    # accumulated before the disable above.
    dut.en.value = 1
    await RisingEdge(dut.valid)
    expected = _expected_edges(osc_period_ns)
    got = int(dut.count.value)
    assert abs(got - expected) <= 2, f"expected ~{expected:.1f} got {got}"


# req: REQ-VALID
@cocotb.test()
async def test_valid_latches_and_stays_high(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut)
    assert int(dut.valid.value) == 0
    osc_period_ns = 97
    cocotb.start_soon(_drive_osc(dut, osc_period_ns))
    dut.en.value = 1
    await RisingEdge(dut.valid)
    assert int(dut.valid.value) == 1
    # a full second window must run through with valid never dropping
    for _ in range(WINDOW_CYCLES):
        await FallingEdge(dut.clk)
        assert int(dut.valid.value) == 1
