"""Held-out tests for counter8: corners the visible suite does not exercise.
Written from spec/spec.yaml before any RTL."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cocotb  # noqa: E402
from cocotb.triggers import FallingEdge, Timer  # noqa: E402

from counter8_common import Bench, read_count  # noqa: E402


# req: REQ-RST-PULSE REQ-WRAP
@cocotb.test()
async def test_pulse_at_carry_boundaries(dut):
    """One-clock reset at counts where +1 carries through many bits (127,
    254, 255) and at an all-bits-different pattern (170, 85): each time 0 on
    the pulse edge, 1 on the next."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for target in (127, 254, 255, 170, 85, 1):
        while tb.model.count != target:
            await tb.tick(0)
        got = await tb.tick(1)
        assert got == 0, f"pulse at count {target}: expected 0 got {got}"
        got = await tb.tick(0)
        assert got == 1, f"after pulse at count {target}: expected 1 got {got}"


# req: REQ-RST-PULSE
@cocotb.test()
async def test_back_to_back_pulses(dut):
    """Pulses separated by exactly one low cycle: 0,1,0,1,... then counting."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for _ in range(50):
        await tb.tick(0)
    for n in range(5):
        got = await tb.tick(1)
        assert got == 0, f"pulse {n}: expected 0 got {got}"
        got = await tb.tick(0)
        assert got == 1, f"release {n}: expected 1 got {got}"
    for i in range(2, 10):
        got = await tb.tick(0)
        assert got == i, f"counting after pulses: expected {i} got {got}"


# req: REQ-WRAP REQ-INC
@cocotb.test()
async def test_three_full_wraps(dut):
    """Free-run 3 * 256 + 10 edges: every edge matches the model, each wrap
    lands on 0, and every count bit is seen at both 0 and 1."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    wraps = 0
    prev = 0
    ones, zeros = 0, 0
    for _ in range(3 * 256 + 10):
        got = await tb.tick(0)
        if prev == 255:
            assert got == 0, f"wrap: expected 0 after 255 got {got}"
            wraps += 1
        ones |= got
        zeros |= (~got) & 0xFF
        prev = got
    assert wraps == 3, f"expected 3 wraps, saw {wraps}"
    assert ones == 0xFF and zeros == 0xFF, (
        f"bits never seen high: {~ones & 0xFF:#04x}, never low: {~zeros & 0xFF:#04x}")


# req: REQ-RST-SYNC
@cocotb.test()
async def test_rst_drop_between_edges_while_held(dut):
    """rst held high but dropped low briefly between edges (back high before
    the next rising edge): count stays 0 throughout. And a between-edge pulse
    at count 255 (all bits set) leaves it unchanged."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for _ in range(4):
        dut.rst.value = 0
        await Timer(1, unit="ns")
        got = read_count(dut)
        assert got == 0, f"rst dropped between edges changed count: got {got}"
        dut.rst.value = 1
        await FallingEdge(dut.clk)
        got = read_count(dut)
        assert got == 0, f"rst high at edge after a between-edge drop: expected 0 got {got}"
        tb.model.step(1)
    while tb.model.count != 255:
        await tb.tick(0)
    dut.rst.value = 1
    await Timer(1, unit="ns")
    dut.rst.value = 0
    await Timer(2, unit="ns")
    got = read_count(dut)
    assert got == 255, f"between-edge pulse at 255 changed count to {got}"
    await FallingEdge(dut.clk)
    got = read_count(dut)
    assert got == 0, f"255 with rst low at edge should wrap to 0, got {got}"
    tb.model.step(0)
    await tb.tick(0)


# req: REQ-INC REQ-RST-SYNC REQ-WRAP REQ-RST-PULSE
@cocotb.test()
async def test_random_reset_pattern_vs_model(dut):
    """2000 edges of seeded random rst (short pulses, long holds, long runs
    past 255), checked edge by edge against the reference model."""
    rng = random.Random(0xC8)
    tb = Bench(dut)
    await tb.start()
    await tb.reset(1)
    edges = 0
    while edges < 2000:
        run = rng.choice((1, 2, 3, 7, 40, 260, 300))
        hold = rng.choice((1, 1, 1, 2, 5))
        for _ in range(run):
            await tb.tick(0)
        for _ in range(hold):
            await tb.tick(1)
        edges += run + hold
