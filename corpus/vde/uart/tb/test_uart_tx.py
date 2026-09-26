"""Visible tests for uart_tx (docs/design.md section 2). Same FallingEdge-
only discipline as corpus/vde/counter8/tb/test_counter8.py - see that file's
note. Checks the parity bit's VALUE only for bytes with data[7] == 0; the
other half is left to holdout/test_uart_tx_holdout.py, which checks every
byte, and is where the `holdout` gate's own corpus fault hides (gates.yaml:
"UART parity inverted where the visible tests do not look"). Checking half
kills the parity mutants that `mutate` would otherwise count as survivors."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

CLK_PERIOD_NS = 10
CLKS_PER_BIT = 4
N_SLOTS = 11  # start + 8 data + parity + stop
START_TIMEOUT = 3 * CLKS_PER_BIT  # clocks from the start pulse to tx falling


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


async def wait_start_bit(dut):
    """Return at the first falling edge where tx is 0 - the start bit's first
    clock. spec.md says a start pulse "begins a frame" but not how many
    clocks later the start bit reaches tx, so this waits for it (up to
    START_TIMEOUT clocks) the way a UART receiver does, rather than pinning
    one RTL's latency."""
    for _ in range(START_TIMEOUT):
        if int(dut.tx.value) == 0:
            return
        await FallingEdge(dut.clk)
    assert int(dut.tx.value) == 0, \
        f"no start bit on tx within {START_TIMEOUT} clocks of the start pulse"


async def read_frame(dut, clks_per_bit=CLKS_PER_BIT, n_slots=N_SLOTS):
    """[start, d0..d7, parity, stop] - tx sampled once per bit-slot, at the
    slot's middle clock, counted from the start bit's falling edge. Returns
    at the stop slot's middle."""
    await wait_start_bit(dut)
    bits = []
    for slot in range(n_slots):
        wait = clks_per_bit // 2 if slot == 0 else clks_per_bit
        for _ in range(wait):
            await FallingEdge(dut.clk)
        bits.append(int(dut.tx.value))
    return bits


async def wait_idle(dut, timeout=CLKS_PER_BIT):
    """Return at the first falling edge where busy is 0."""
    for _ in range(timeout):
        if int(dut.busy.value) == 0:
            return
        await FallingEdge(dut.clk)
    assert int(dut.busy.value) == 0, "busy never dropped after the stop bit"


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
        if value < 128:  # data[7] == 1 is the holdout's alone
            parity = sum(data_bits(value)) & 1
            assert bits[9] == parity, \
                f"value={value}: parity bit was {bits[9]}, want {parity}"
        await wait_idle(dut)
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
        await wait_idle(dut)


# req: REQ-BUSY
@cocotb.test()
async def test_busy_spans_the_whole_frame(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)
    assert int(dut.busy.value) == 0
    for value in (0b01010101, 0b00000000, 0b11111111):
        await send(dut, value)
        await wait_start_bit(dut)
        # busy holds through every slot, sampled at each slot's middle ...
        for slot in range(11):  # start + 8 data + parity + stop
            wait = CLKS_PER_BIT // 2 if slot == 0 else CLKS_PER_BIT
            for _ in range(wait):
                await FallingEdge(dut.clk)
            assert int(dut.busy.value) == 1, \
                f"value={value} slot={slot}: busy was {int(dut.busy.value)}"
        # ... and is 0 by the first clock after the stop slot's hold.
        for _ in range(CLKS_PER_BIT - CLKS_PER_BIT // 2):
            await FallingEdge(dut.clk)
        assert int(dut.busy.value) == 0, \
            f"value={value}: busy still 1 after the stop bit's hold"
        await FallingEdge(dut.clk)
