"""Visible tests for uart_tx (docs/design.md section 2). Same FallingEdge-
only discipline as corpus/vde/counter8/tb/test_counter8.py - see that file's
note. Deliberately does NOT check the parity bit's VALUE (only that a frame
has the right shape around it) - see holdout/test_uart_tx_holdout.py, which
does, and is what the `holdout` gate's own corpus fault targets (gates.yaml:
"UART parity inverted where the visible tests do not look")."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

CLK_PERIOD_NS = 10
CLKS_PER_BIT = 4
N_SLOTS = 11  # start + 8 data + parity + stop


async def reset(dut):
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    dut.start.value = 0
    dut.data.value = 0
    for _ in range(3):
        await FallingEdge(dut.clk)
    dut.rst.value = 0
    await FallingEdge(dut.clk)


async def send(dut, data):
    dut.data.value = data
    dut.start.value = 1
    await FallingEdge(dut.clk)
    dut.start.value = 0


async def read_frame(dut, clks_per_bit=CLKS_PER_BIT, n_slots=N_SLOTS):
    """[start, d0..d7, parity, stop] - tx sampled once per bit-slot, at the
    slot's last clock (verified stable there in this design: `tx` only
    changes at a slot boundary, never mid-slot)."""
    bits = []
    for _ in range(n_slots):
        for _ in range(clks_per_bit):
            await FallingEdge(dut.clk)
        bits.append(int(dut.tx.value))
    return bits


def data_bits(value):
    return [(value >> i) & 1 for i in range(8)]


# req: REQ-FRAME
@cocotb.test()
async def test_frame_start_data_stop(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)
    for value in range(256):
        await send(dut, value)
        bits = await read_frame(dut)
        start, data, stop = bits[0], bits[1:9], bits[10]
        assert start == 0, f"value={value}: start bit was {start}"
        assert data == data_bits(value), f"value={value}: bad bits {data}"
        assert stop == 1, f"value={value}: stop bit was {stop}"
        await FallingEdge(dut.clk)  # a clock of idle margin between frames


# req: REQ-FRAME
@cocotb.test()
async def test_back_to_back_frames_no_idle_gap(dut):
    """Same shape check as test_frame_start_data_stop, but the next frame's
    start pulse lands the instant busy drops - no idle margin at all."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)
    for value in (0b10110100, 0b01010101, 0b00000000, 0b11111111, 0b00000001):
        await send(dut, value)
        bits = await read_frame(dut)
        start, data, stop = bits[0], bits[1:9], bits[10]
        assert start == 0, f"value={value}: start bit was {start}"
        assert data == data_bits(value), f"value={value}: bad bits {data}"
        assert stop == 1, f"value={value}: stop bit was {stop}"


# req: REQ-BUSY
@cocotb.test()
async def test_busy_spans_the_whole_frame(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)
    assert int(dut.busy.value) == 0
    for value in (0b01010101, 0b00000000, 0b11111111):
        await send(dut, value)
        for slot in range(11):  # start + 8 data + parity + stop
            for _ in range(CLKS_PER_BIT):
                await FallingEdge(dut.clk)
            expect_busy = 0 if slot == 10 else 1
            assert int(dut.busy.value) == expect_busy, \
                f"value={value} slot={slot}: busy was {int(dut.busy.value)}"
        await FallingEdge(dut.clk)
