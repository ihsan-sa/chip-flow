"""Held-out test for uart_tx (docs/design.md section 2). Not part of tb/,
never given to the rtl-writer or fixer agent's file list - only run by the
`holdout` gate. Checks the one thing tb/test_uart_tx.py deliberately never
looks at: the parity bit's actual VALUE (even parity - XOR of the 8 data
bits). gates.yaml's own fault for this gate: "UART parity inverted where the
visible tests do not look"."""
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
    bits = []
    for _ in range(n_slots):
        for _ in range(clks_per_bit):
            await FallingEdge(dut.clk)
        bits.append(int(dut.tx.value))
    return bits


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
        await FallingEdge(dut.clk)
