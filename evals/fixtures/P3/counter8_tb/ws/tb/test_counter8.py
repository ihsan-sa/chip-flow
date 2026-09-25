"""Visible tests for counter8, written from spec/spec.yaml before any RTL."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cocotb  # noqa: E402
from cocotb.triggers import FallingEdge, Timer  # noqa: E402

from counter8_common import Bench, read_count  # noqa: E402


# req: REQ-RST-SYNC
@cocotb.test()
async def test_reset_held_keeps_zero(dut):
    """rst held high: count is 0 after every rising edge, for many edges."""
    tb = Bench(dut)
    await tb.start()
    for i in range(20):
        got = await tb.tick(1)
        assert got == 0, f"reset edge {i}: expected count=0 got {got}"


# req: REQ-INC
@cocotb.test()
async def test_increments_by_one(dut):
    """After reset, count goes 1, 2, 3, ... one per rising edge."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for i in range(1, 101):
        got = await tb.tick(0)
        assert got == i, f"edge {i} after reset release: expected {i} got {got}"


# req: REQ-RST-SYNC
@cocotb.test()
async def test_reset_is_synchronous(dut):
    """rst rising between edges does not clear count until the next rising
    edge, and a rst pulse that starts and ends between two edges has no
    effect at all."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for _ in range(9):
        await tb.tick(0)
    before = read_count(dut)
    assert before == 9, f"expected count=9 before glitch, got {before}"

    # A pulse wholly between two rising edges (we are at a falling edge).
    dut.rst.value = 1
    await Timer(1, unit="ns")
    got = read_count(dut)
    assert got == before, f"rst high between edges changed count: {before} -> {got}"
    await Timer(1, unit="ns")
    dut.rst.value = 0
    await Timer(1, unit="ns")
    got = read_count(dut)
    assert got == before, f"rst pulse between edges changed count: {before} -> {got}"
    await FallingEdge(dut.clk)
    got = read_count(dut)
    assert got == before + 1, (
        f"rst low at the edge after a between-edge pulse: expected {before + 1} got {got}")
    tb.model.step(0)

    # rst raised at a falling edge: count holds until the rising edge, then 0.
    dut.rst.value = 1
    await Timer(2, unit="ns")
    got = read_count(dut)
    assert got == before + 1, f"count cleared before the rising edge: got {got}"
    await FallingEdge(dut.clk)
    got = read_count(dut)
    assert got == 0, f"rst high at the rising edge: expected 0 got {got}"
    tb.model.step(1)
    await tb.tick(0)


# req: REQ-WRAP REQ-INC
@cocotb.test()
async def test_wraps_255_to_0(dut):
    """Count runs 0..255, wraps to 0 and keeps counting through 0, 1, 2 ..."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    seen = []
    for _ in range(260):
        seen.append(await tb.tick(0))
    idx = seen.index(255)
    assert idx == 254, f"expected 255 on edge 255 after reset, found at {idx + 1}"
    assert seen[idx + 1:idx + 5] == [0, 1, 2, 3], (
        f"after 255 expected [0, 1, 2, 3] got {seen[idx + 1:idx + 5]}")


# req: REQ-RST-PULSE
@cocotb.test()
async def test_one_cycle_reset_mid_run(dut):
    """A one-clock rst while counting clears count on the next edge; count is
    1 on the edge after release."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for _ in range(37):
        await tb.tick(0)
    assert tb.model.count == 37
    got = await tb.tick(1)
    assert got == 0, f"one-clock reset at count 37: expected 0 got {got}"
    got = await tb.tick(0)
    assert got == 1, f"first edge after release: expected 1 got {got}"
    for i in range(2, 12):
        got = await tb.tick(0)
        assert got == i, f"counting after pulse: expected {i} got {got}"
