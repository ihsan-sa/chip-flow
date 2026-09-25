"""Visible tests for dac_tile_digital, written from spec.md alone: every
code loads through the register one clock after it is presented, and reset
clears it whatever code_in holds."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

CLK_PERIOD_NS = 20


async def _reset(dut, cycles=3):
    dut.rst_n.value = 0
    dut.code_in.value = 0
    for _ in range(cycles):
        await FallingEdge(dut.clk)
    dut.rst_n.value = 1


def _outputs(dut):
    return (int(dut.bmsb.value) << 1) | int(dut.blsb.value)


# req: REQ-LOAD
@cocotb.test()
async def test_every_code_loads(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut)
    for code in (1, 2, 3, 0, 2, 1):
        dut.code_in.value = code
        await FallingEdge(dut.clk)
        assert _outputs(dut) == code, f"code {code:02b}: got {_outputs(dut):02b}"


# req: REQ-LOAD
@cocotb.test()
async def test_code_holds_between_edges(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut)
    dut.code_in.value = 3
    await FallingEdge(dut.clk)
    assert _outputs(dut) == 3
    # a change between edges is not seen until the next rising edge
    dut.code_in.value = 0
    assert _outputs(dut) == 3


# req: REQ-RESET
@cocotb.test()
async def test_reset_clears_code(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut)
    dut.code_in.value = 3
    await FallingEdge(dut.clk)
    assert _outputs(dut) == 3
    dut.rst_n.value = 0
    await FallingEdge(dut.clk)
    assert _outputs(dut) == 0, "rst_n low must clear the code with code_in at 11"
