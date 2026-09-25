"""Held-out tests for spi_fifo (docs/design.md section 2) - never given to
the rtl-writer. The visible tb/ only ever drains the FIFO to empty before
sending again, or fills it once; this file interleaves pushes and pops
across the pointer wraparound, which a wr_ptr/rd_ptr off-by-one (comparing
the wrong bits, or wrapping the extra bit at the wrong point) can survive
the visible suite and still get wrong."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

CLK_PERIOD_NS = 10
SETTLE = 2


async def reset(dut):
    dut.rst.value = 1
    dut.sclk.value = 0
    dut.mosi.value = 0
    dut.cs_n.value = 1
    dut.rd_en.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def settle(dut, n=SETTLE):
    for _ in range(n):
        await RisingEdge(dut.clk)


async def spi_send_byte(dut, value):
    dut.cs_n.value = 0
    await settle(dut)
    for i in range(7, -1, -1):
        dut.mosi.value = (value >> i) & 1
        await settle(dut)
        dut.sclk.value = 1
        await settle(dut)
        dut.sclk.value = 0
        await settle(dut)
    dut.cs_n.value = 1
    await settle(dut)


async def pop_byte(dut):
    dut.rd_en.value = 1
    await RisingEdge(dut.clk)
    dut.rd_en.value = 0
    await RisingEdge(dut.clk)
    return int(dut.rd_data.value)


# req: REQ-POP REQ-FULL REQ-EMPTY
@cocotb.test()
async def test_interleaved_push_pop_across_pointer_wrap(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)

    expected = []
    seq = list(range(0, 40, 3))  # more than 4x the FIFO depth
    i = 0
    while i < len(seq) or expected:
        # push up to 2 bytes if there is room and input left
        for _ in range(2):
            if i < len(seq) and int(dut.full.value) == 0:
                await spi_send_byte(dut, seq[i] & 0xFF)
                expected.append(seq[i] & 0xFF)
                i += 1
        # pop 1 byte if there is one
        if expected:
            got = await pop_byte(dut)
            want = expected.pop(0)
            assert got == want, \
                f"pointer wraparound bug: expected {want:#x}, popped {got:#x}"

    assert int(dut.empty.value) == 1
    assert int(dut.full.value) == 0
