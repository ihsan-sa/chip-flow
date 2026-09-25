"""Held-out test for uart_tx (docs/design.md section 2). Not part of tb/,
never given to the rtl-writer or fixer agent's file list - only run by the
`holdout` gate. Checks the one thing tb/test_uart_tx.py deliberately never
looks at: the parity bit's actual VALUE (even parity - XOR of the 8 data
bits). gates.yaml's own fault for this gate: "UART parity inverted where the
visible tests do not look".

The frame is read the way a real UART receiver reads it: wait for the start
bit's falling edge on tx, then sample each slot at its middle. spec.md says a
start pulse "begins a frame" but never how many clocks later the start bit
appears on tx, so the reader tolerates a short latency (START_TIMEOUT clocks)
rather than pinning the reference RTL's own."""
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
    clock - or fail if it never comes within START_TIMEOUT clocks."""
    for _ in range(START_TIMEOUT):
        if int(dut.tx.value) == 0:
            return
        await FallingEdge(dut.clk)
    assert int(dut.tx.value) == 0, \
        f"no start bit on tx within {START_TIMEOUT} clocks of the start pulse"


async def read_frame(dut, clks_per_bit=CLKS_PER_BIT, n_slots=N_SLOTS):
    """[start, d0..d7, parity, stop] - tx sampled once per slot, at the
    slot's middle clock, counted from the start bit's falling edge."""
    await wait_start_bit(dut)
    bits = []
    for slot in range(n_slots):
        wait = clks_per_bit // 2 if slot == 0 else clks_per_bit
        for _ in range(wait):
            await FallingEdge(dut.clk)
        bits.append(int(dut.tx.value))
    return bits


async def wait_idle(dut, timeout=START_TIMEOUT):
    for _ in range(timeout):
        if int(dut.busy.value) == 0:
            return
        await FallingEdge(dut.clk)
    assert int(dut.busy.value) == 0, "busy never dropped after the stop bit"


# req: REQ-FRAME
@cocotb.test()
async def test_parity_is_even(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)
    for value in (0b10110100, 0b00000000, 0b11111111, 0b00000001, 0b00000011):
        await send(dut, value)
        bits = await read_frame(dut)
        parity = bits[9]
        expected = bin(value).count("1") % 2
        assert parity == expected, f"parity for {value:#010b} was {parity}"
        await wait_idle(dut)
        await FallingEdge(dut.clk)
