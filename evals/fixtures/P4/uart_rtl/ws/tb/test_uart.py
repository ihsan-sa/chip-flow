"""Visible cocotb tests for the uart transmitter, written from spec/ alone.

Every drive and every sample happens on a FallingEdge: inputs set at a
falling edge are sampled by the next rising edge, and outputs read at a
falling edge are the registered values from the rising edge before it.
The DUT is compared against uart_model.UartModel on every clock, and the
frame-level tests additionally decode the tx line on their own.
"""
import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uart_model import (DEFAULT_CLKS_PER_BIT, FRAME_BITS, UartModel,  # noqa: E402
                        decode_frame, frame_bits, parity_even)

CLK_PERIOD_NS = 10


def clks_per_bit(dut) -> int:
    """The DUT's CLKS_PER_BIT if the simulator exposes it, else the spec
    default (spec.yaml params.CLKS_PER_BIT = 4)."""
    try:
        v = int(dut.CLKS_PER_BIT.value)
        if v >= 1:
            return v
    except Exception:
        pass
    return DEFAULT_CLKS_PER_BIT


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.model = UartModel(clks_per_bit(dut))
        self.cpb = self.model.cpb
        self.cycle = 0
        self.trace: list[tuple[int, int]] = []   # (tx, busy) per clock

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, CLK_PERIOD_NS, unit="ns").start())
        self.dut.rst.value = 0
        self.dut.start.value = 0
        self.dut.data.value = 0
        await FallingEdge(self.dut.clk)

    def read(self) -> tuple[int, int]:
        tx = self.dut.tx.value
        busy = self.dut.busy.value
        assert tx.is_resolvable and busy.is_resolvable, (
            f"clock {self.cycle}: tx={tx} busy={busy} not 0/1 "
            f"(model: {self.model.where()})")
        return int(tx), int(busy)

    async def clock(self, rst=0, start=0, data=None, check=True):
        """Drive inputs for one rising edge, step the model, then check the
        DUT's outputs against it at the following falling edge."""
        if data is not None:
            self.dut.data.value = data & 0xFF
            sampled_data = data & 0xFF
        else:
            sampled_data = int(self.dut.data.value)
        self.dut.rst.value = rst
        self.dut.start.value = start
        await FallingEdge(self.dut.clk)
        self.model.step(rst, start, sampled_data)
        self.cycle += 1
        got = self.read()
        self.trace.append(got)
        if check:
            exp = self.model.outputs()
            assert got == exp, (
                f"clock {self.cycle}: expected (tx, busy) = {exp}, got {got} "
                f"at {self.model.where()} (rst={rst} start={start} "
                f"data=0x{sampled_data:02x})")
        return got

    async def reset(self, cycles=2):
        for _ in range(cycles):
            await self.clock(rst=1, start=0)
        await self.clock(rst=0, start=0)

    async def idle(self, n, data=None):
        for _ in range(n):
            await self.clock(start=0, data=data)

    async def send(self, byte, data_during=None):
        """Pulse start for one clock with `byte`, then run the whole frame.
        data_during(i) -> value put on data at frame clock i (or None to
        leave it). Returns the (tx, busy) trace of the frame's clocks,
        starting with the first clock of the frame."""
        mark = len(self.trace)
        await self.clock(start=1, data=byte)
        for i in range(1, self.model.frame_len):
            d = data_during(i) if data_during else None
            await self.clock(start=0, data=d)
        return self.trace[mark:]

    def decode(self, frame_trace, byte):
        """Independent frame decode: busy high for the whole frame, each of
        the 11 bits a constant run of exactly cpb clocks, and the bit values
        equal to frame_bits(byte)."""
        cpb = self.cpb
        assert len(frame_trace) == FRAME_BITS * cpb
        busy = [b for _, b in frame_trace]
        assert all(busy), (
            f"busy dropped inside the frame for 0x{byte:02x}: {busy}")
        tx = [t for t, _ in frame_trace]
        line = []
        for k in range(FRAME_BITS):
            chunk = tx[k * cpb:(k + 1) * cpb]
            assert len(set(chunk)) == 1, (
                f"bit {k} of the frame for 0x{byte:02x} is not held for "
                f"{cpb} clocks: tx over its slot = {chunk}")
            line.append(chunk[0])
        exp = frame_bits(byte)
        assert line == exp, (
            f"frame for 0x{byte:02x}: expected line bits {exp} "
            f"({decode_frame(exp)}), got {line} ({decode_frame(line)})")


# req: REQ-IDLE REQ-RESET
@cocotb.test()
async def test_idle_after_reset(dut):
    """After reset, and with start held low, tx stays 1 and busy 0 whatever
    data does."""
    tb = Bench(dut)
    await tb.start()
    await tb.clock(rst=1)
    tx, busy = tb.read()
    assert (tx, busy) == (1, 0), (
        f"one clock after rst sampled high: expected tx=1 busy=0, got "
        f"tx={tx} busy={busy}")
    await tb.clock(rst=0)
    rng = random.Random(1)
    for _ in range(3 * FRAME_BITS * tb.cpb):
        await tb.clock(start=0, data=rng.randrange(256))
        tx, busy = tb.read()
        assert (tx, busy) == (1, 0), (
            f"idle with start low: expected tx=1 busy=0, got tx={tx} "
            f"busy={busy}")


# req: REQ-START-BIT REQ-DATA-LSB-FIRST REQ-PARITY-EVEN REQ-STOP-BIT REQ-BIT-HOLD REQ-BUSY-FRAME
@cocotb.test()
async def test_single_frame(dut):
    """One frame of 0xA5: start bit, LSB-first data, even parity, stop bit,
    each held CLKS_PER_BIT clocks, busy high for exactly 11*CLKS_PER_BIT
    clocks and low on the next."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    byte = 0xA5
    trace = await tb.send(byte)
    tb.decode(trace, byte)
    assert trace[0] == (0, 1), (
        f"first clock of the frame: expected tx=0 (start bit) busy=1, got "
        f"tx={trace[0][0]} busy={trace[0][1]}")
    await tb.clock()
    tx, busy = tb.read()
    assert (tx, busy) == (1, 0), (
        f"clock after the stop bit's hold: expected tx=1 busy=0, got "
        f"tx={tx} busy={busy}")
    await tb.idle(2 * tb.cpb)


# req: REQ-DATA-LSB-FIRST REQ-PARITY-EVEN REQ-BIT-HOLD REQ-BUSY-FRAME
@cocotb.test()
async def test_byte_patterns(dut):
    """A spread of bytes with odd and even parity, walking ones, all-zero
    and all-one, each checked by the model and by an independent decode."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    rng = random.Random(7)
    bytes_ = [0x00, 0xFF, 0x01, 0x80, 0x03, 0xC0, 0x55, 0xAA, 0x7F, 0xFE,
              0x10, 0x96] + [rng.randrange(256) for _ in range(8)]
    assert {parity_even(b) for b in bytes_} == {0, 1}
    for b in bytes_:
        trace = await tb.send(b)
        tb.decode(trace, b)
        await tb.idle(3)


# req: REQ-LATCH-ON-START
@cocotb.test()
async def test_latch_on_start(dut):
    """data is sampled on the start clock only: changing it during the frame
    does not change the bits sent."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for byte, other in ((0x3C, 0xC3), (0x81, 0x7E)):
        # hold the byte, then flip data to a different value half-way
        # through the data bits and leave it there
        trace = await tb.send(
            byte, data_during=lambda i: other if i >= 4 * tb.cpb else None)
        tb.decode(trace, byte)
        await tb.idle(2, data=other)


# req: REQ-RESET REQ-IDLE
@cocotb.test()
async def test_reset_mid_frame(dut):
    """rst held high for several clocks in the middle of a frame abandons it:
    idle on the clock after rst is first sampled, no resumption after rst
    drops, and the next frame is sent in full."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    await tb.clock(start=1, data=0x00)
    for _ in range(3 * tb.cpb + 1):
        await tb.clock()
    tx, busy = tb.read()
    assert busy == 1 and tx == 0, (
        f"mid-frame of 0x00: expected tx=0 busy=1, got tx={tx} busy={busy}")
    await tb.clock(rst=1)
    tx, busy = tb.read()
    assert (tx, busy) == (1, 0), (
        f"clock after rst mid-frame: expected tx=1 busy=0, got tx={tx} "
        f"busy={busy}")
    await tb.clock(rst=1)
    await tb.clock(rst=1)
    await tb.idle(FRAME_BITS * tb.cpb + 2)
    trace = await tb.send(0x5A)
    tb.decode(trace, 0x5A)
    await tb.idle(2)


# req: REQ-STOP-BIT REQ-IDLE REQ-BUSY-FRAME
@cocotb.test()
async def test_line_high_between_frames(dut):
    """tx is continuously 1 from the start of the stop bit through the idle
    gap until the next frame's start bit, and busy is 0 in the gap."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for byte, gap in ((0x00, 5), (0xFE, 1), (0x7F, 9)):
        trace = await tb.send(byte)
        stop = [t for t, _ in trace[-tb.cpb:]]
        assert stop == [1] * tb.cpb, (
            f"stop bit of 0x{byte:02x}: expected tx=1 for {tb.cpb} clocks, "
            f"got {stop}")
        mark = len(tb.trace)
        await tb.idle(gap, data=~byte)
        gap_trace = tb.trace[mark:]
        assert gap_trace == [(1, 0)] * gap, (
            f"idle gap after 0x{byte:02x}: expected (tx, busy) = (1, 0) on "
            f"all {gap} clocks, got {gap_trace}")
