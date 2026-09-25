"""Held-out test for rv_core (docs/design.md section 2). Not part of tb/,
never given to the rtl-writer or fixer agent's file list - only run by the
`holdout` gate. Checks the one thing tb/test_rv_core.py deliberately never
tries: SRA and SRAI on a NEGATIVE operand, where an arithmetic shift fills
with ones and a logical one with zeros. Self-contained on purpose - it
imports nothing from tb/, so an edit to the visible bench or its model
cannot weaken it."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

CLK_PERIOD_NS = 10
EBREAK = 0x00100073
MASK = 0xFFFFFFFF


def lui(rd, imm20):
    return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | 0b0110111


def addi(rd, rs1, imm):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (rd << 7) | 0b0010011


def srai(rd, rs1, shamt):
    return (0x20 << 25) | (shamt << 20) | (rs1 << 15) | (5 << 12) \
        | (rd << 7) | 0b0010011


def sra(rd, rs1, rs2):
    return (0x20 << 25) | (rs2 << 20) | (rs1 << 15) | (5 << 12) \
        | (rd << 7) | 0b0110011


def li(rd, value):
    lo = value & 0xFFF
    lo = lo - 0x1000 if lo & 0x800 else lo
    return [lui(rd, ((value - lo) >> 12) & 0xFFFFF), addi(rd, rd, lo)]


def arith_shift(value, n):
    signed = value - (1 << 32) if value >> 31 else value
    return (signed >> n) & MASK


async def reset(dut):
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    dut.we.value = 0
    dut.run.value = 0
    dut.din.value = 0
    for _ in range(2):
        await FallingEdge(dut.clk)
    dut.rst.value = 0


async def run_program(dut, words):
    await reset(dut)
    for w in words:
        for k in range(4):
            dut.din.value = (w >> (8 * k)) & 0xFF
            dut.we.value = 1
            await FallingEdge(dut.clk)
    dut.we.value = 0
    dut.run.value = 1
    for _ in range(8 * len(words) + 8):
        await FallingEdge(dut.clk)
        if int(dut.halted.value):
            break
    dut.run.value = 0
    assert int(dut.halted.value) == 1, "core did not halt"
    assert int(dut.illegal.value) == 0, "core flagged a legal program illegal"


async def read_reg(dut, index):
    value = 0
    for k in range(4):
        dut.din.value = (index << 2) | k
        await Timer(1, unit="ns")
        value |= int(dut.dout.value) << (8 * k)
    return value


# req: REQ-ALU
@cocotb.test()
async def test_sra_fills_with_the_sign_bit(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    for value in (0x80000000, 0xFFFFFFFF, 0xF0F0F0F0, 0x9ABCDEF0):
        for n in (1, 4, 13, 31):
            words = li(1, value) + li(4, 0x40 | n) + [
                srai(2, 1, n),
                sra(3, 1, 4),       # only rs2[4:0] counts: 0x40 | n shifts n
                EBREAK]
            await run_program(dut, words)
            want = arith_shift(value, n)
            got2, got3 = await read_reg(dut, 2), await read_reg(dut, 3)
            assert got2 == want, f"srai {value:#010x},{n}: {got2:#010x}"
            assert got3 == want, f"sra {value:#010x},{n}: {got3:#010x}"
