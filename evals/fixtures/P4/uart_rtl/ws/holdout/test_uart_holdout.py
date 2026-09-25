"""Held-out cocotb tests for the uart transmitter, written from spec/ alone.

Each test exercises a corner the visible tb/ suite deliberately leaves
alone: every byte value, frames with no idle gap, a one-clock reset at
every point of a frame, rst and start on the same edge, start pulses on
every clock of a frame in flight (including its last), and data changing
on every clock from the one right after the latch.

Same discipline as tb/: drive and sample on FallingEdge only, and compare
tx/busy with the cycle model on every clock.
"""
import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uart_holdout_model import (DEFAULT_CLKS_PER_BIT, FRAME_BITS,  # noqa: E402
                                UartModel, decode_frame, frame_bits)

CLK_PERIOD_NS = 10


def clks_per_bit(dut) -> int:
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
        self.trace: list[tuple[int, int]] = []

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, CLK_PERIOD_NS, unit="ns").start())
        self.dut.rst.value = 0
        self.dut.start.value = 0
        self.dut.data.value = 0
        await FallingEdge(self.dut.clk)

    def read(self):
        tx = self.dut.tx.value
        busy = self.dut.busy.value
        assert tx.is_resolvable and busy.is_resolvable, (
            f"clock {self.cycle}: tx={tx} busy={busy} not 0/1 "
            f"(model: {self.model.where()})")
        return int(tx), int(busy)

    async def clock(self, rst=0, start=0, data=None):
        if data is not None:
            self.dut.data.value = data & 0xFF
            sampled = data & 0xFF
        else:
            sampled = int(self.dut.data.value)
        self.dut.rst.value = rst
        self.dut.start.value = start
        await FallingEdge(self.dut.clk)
        self.model.step(rst, start, sampled)
        self.cycle += 1
        got = self.read()
        self.trace.append(got)
        exp = self.model.outputs()
        assert got == exp, (
            f"clock {self.cycle}: expected (tx, busy) = {exp}, got {got} at "
            f"{self.model.where()} (rst={rst} start={start} "
            f"data=0x{sampled:02x})")
        return got

    async def reset(self):
        await self.clock(rst=1)
        await self.clock(rst=1)
        await self.clock()

    async def idle(self, n, data=None):
        for _ in range(n):
            await self.clock(data=data)

    def decode(self, frame_trace, byte):
        cpb = self.cpb
        assert len(frame_trace) == FRAME_BITS * cpb
        assert all(b for _, b in frame_trace), (
            f"busy dropped inside the frame for 0x{byte:02x}")
        tx = [t for t, _ in frame_trace]
        line = []
        for k in range(FRAME_BITS):
            chunk = tx[k * cpb:(k + 1) * cpb]
            assert len(set(chunk)) == 1, (
                f"bit {k} of the frame for 0x{byte:02x} not held {cpb} "
                f"clocks: {chunk}")
            line.append(chunk[0])
        exp = frame_bits(byte)
        assert line == exp, (
            f"frame for 0x{byte:02x}: expected {exp} ({decode_frame(exp)}), "
            f"got {line} ({decode_frame(line)})")


# req: REQ-PARITY-EVEN REQ-DATA-LSB-FIRST REQ-BIT-HOLD REQ-BUSY-FRAME REQ-STOP-BIT
@cocotb.test()
async def test_all_bytes_back_to_back(dut):
    """All 256 byte values, each frame's start pulse on the first clock busy
    reads 0 after the previous frame (no idle gap beyond the spec's
    minimum). Every parity case and every data bit in both states."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for byte in range(256):
        mark = len(tb.trace)
        await tb.clock(start=1, data=byte)
        for _ in range(tb.model.frame_len - 1):
            await tb.clock()
        tb.decode(tb.trace[mark:], byte)
        # the clock after the stop bit's hold: busy reads 0, tx 1, and the
        # next start pulse is driven right here - the first edge on which
        # busy is 0 - with no extra gap
        tx, busy = await tb.clock()
        assert (tx, busy) == (1, 0), (
            f"clock after the 0x{byte:02x} frame: expected tx=1 busy=0, got "
            f"tx={tx} busy={busy}")
    assert tb.model.frames_started == list(range(256))


# req: REQ-RESET
@cocotb.test()
async def test_one_clock_reset_every_offset(dut):
    """A single-clock rst at every clock of a frame (start bit through the
    stop bit's last hold clock) abandons it: idle on the next clock, no
    resumption, and the following frame is sent correctly."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    frame_len = tb.model.frame_len
    for offset in range(frame_len):
        byte = (0x5B + 37 * offset) & 0xFF
        await tb.clock(start=1, data=byte)
        for _ in range(offset):
            await tb.clock()
        tx, busy = await tb.clock(rst=1)
        assert (tx, busy) == (1, 0), (
            f"rst for one clock at frame clock {offset}: expected tx=1 "
            f"busy=0 on the next clock, got tx={tx} busy={busy}")
        # no resumption of the abandoned frame
        await tb.idle(frame_len + 1)
        mark = len(tb.trace)
        await tb.clock(start=1, data=~byte)
        for _ in range(frame_len - 1):
            await tb.clock()
        tb.decode(tb.trace[mark:], (~byte) & 0xFF)
        await tb.clock()


# req: REQ-RESET REQ-IDLE
@cocotb.test()
async def test_reset_and_start_same_edge(dut):
    """start sampled on the same edge as rst is lost: no frame begins,
    before or after rst is released. A start on the first clock after rst
    drops is accepted normally."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for byte in (0x00, 0xA5, 0xFF):
        tx, busy = await tb.clock(rst=1, start=1, data=byte)
        assert (tx, busy) == (1, 0), (
            f"rst and start on the same edge (data 0x{byte:02x}): expected "
            f"idle, got tx={tx} busy={busy}")
        for _ in range(FRAME_BITS * tb.cpb + 2):
            tx, busy = await tb.clock()
            assert (tx, busy) == (1, 0), (
                f"a start lost to rst began a frame anyway: tx={tx} "
                f"busy={busy}")
    # start on the very first clock with rst low
    await tb.clock(rst=1)
    mark = len(tb.trace)
    await tb.clock(start=1, data=0x69)
    for _ in range(tb.model.frame_len - 1):
        await tb.clock()
    tb.decode(tb.trace[mark:], 0x69)
    await tb.idle(2)


# req: REQ-START-WHILE-BUSY REQ-BUSY-FRAME REQ-LATCH-ON-START
@cocotb.test()
async def test_start_on_every_busy_clock(dut):
    """start pulses on every clock of a frame in flight, including its last
    busy clock, each with a different data value: the frame is unchanged,
    busy falls on time and no second frame follows."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    frame_len = tb.model.frame_len
    for phase, byte in ((1, 0x0F), (0, 0xB2)):
        mark = len(tb.trace)
        await tb.clock(start=1, data=byte)
        # one-clock start pulses on alternate clocks; the two frames use
        # opposite phases so together they cover every clock of a frame
        for i in range(1, frame_len):
            await tb.clock(start=int(i % 2 == phase),
                           data=(byte ^ (0x11 * i)) & 0xFF)
        tb.decode(tb.trace[mark:], byte)
        # a start pulse on the edge after the frame's last busy clock is
        # displayed is sampled while busy reads 1, so it is ignored too
        tx, busy = await tb.clock(start=1, data=~byte)
        assert (tx, busy) == (1, 0), (
            f"start sampled on the last busy clock of the 0x{byte:02x} "
            f"frame was not ignored: tx={tx} busy={busy}")
        for _ in range(frame_len + 2):
            tx, busy = await tb.clock(start=0, data=~byte)
            assert (tx, busy) == (1, 0), (
                f"a start pulsed during the 0x{byte:02x} frame was queued "
                f"as a second frame: tx={tx} busy={busy}")
    assert tb.model.frames_started == [0x0F, 0xB2]


# req: REQ-LATCH-ON-START REQ-IDLE
@cocotb.test()
async def test_data_churn_every_clock(dut):
    """data changes on every clock, including the one right after the start
    edge and the idle clocks with start low: only the value present on the
    accepting start edge is sent, and idle stays idle."""
    tb = Bench(dut)
    await tb.start()
    await tb.reset()
    for byte in (0x01, 0x80, 0xE7):
        for i in range(5):
            tx, busy = await tb.clock(start=0, data=byte ^ (0xFF - i))
            assert (tx, busy) == (1, 0)
        mark = len(tb.trace)
        await tb.clock(start=1, data=byte)
        for i in range(1, tb.model.frame_len):
            await tb.clock(data=(~byte if i % 2 else byte ^ 0x5A) & 0xFF)
        tb.decode(tb.trace[mark:], byte)
        await tb.clock(data=~byte)
