"""Visible tests for spi_fifo (docs/design.md section 2). sclk/cs_n are
driven and settled for whole clk cycles throughout - the design samples
them synchronously in the clk domain (rtl/spi_fifo.v's own docstring), so a
real testbench never needs to race an independent SPI clock against it."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

CLK_PERIOD_NS = 10
SETTLE = 2  # clk edges held between every sclk/cs_n transition


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


async def spi_send_byte(dut, value, deassert_cs=True):
    dut.cs_n.value = 0
    await settle(dut)
    for i in range(7, -1, -1):
        dut.mosi.value = (value >> i) & 1
        await settle(dut)
        dut.sclk.value = 1
        await settle(dut)
        dut.sclk.value = 0
        await settle(dut)
    if deassert_cs:
        dut.cs_n.value = 1
        await settle(dut)


async def pop_byte(dut):
    dut.rd_en.value = 1
    await RisingEdge(dut.clk)
    dut.rd_en.value = 0
    await RisingEdge(dut.clk)
    return int(dut.rd_data.value)


# req: REQ-RX REQ-POP REQ-EMPTY
@cocotb.test()
async def test_rx_then_pop_matches_and_empty_tracks(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)
    assert int(dut.empty.value) == 1

    for value in (0x00, 0xFF, 0xA5, 0x3C, 0x01):
        assert int(dut.empty.value) == 1, "FIFO should drain between sends here"
        await spi_send_byte(dut, value)
        assert int(dut.empty.value) == 0, f"value={value:#x}: empty stayed high after rx"
        got = await pop_byte(dut)
        assert got == value, f"expected {value:#x}, popped {got:#x}"
        assert int(dut.empty.value) == 1, f"value={value:#x}: empty did not return high"


# req: REQ-FULL REQ-RX
@cocotb.test()
async def test_full_and_overflow_drop_does_not_corrupt(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)
    values = [0x11, 0x22, 0x33, 0x44]
    for value in values:
        assert int(dut.full.value) == 0
        await spi_send_byte(dut, value)
    assert int(dut.full.value) == 1, "FIFO should report full after 4 bytes"

    # a 5th byte, received while full, must be dropped: the 4 queued bytes
    # come out unchanged and in order.
    await spi_send_byte(dut, 0x99)
    assert int(dut.full.value) == 1, "overflow byte must not evict anything"

    for expected in values:
        got = await pop_byte(dut)
        assert got == expected, f"expected {expected:#x}, popped {got:#x} " \
                                "(overflow byte corrupted the queue)"
    assert int(dut.empty.value) == 1


# req: REQ-RESET
@cocotb.test()
async def test_reset_clears_partial_frame_and_queue(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)
    await spi_send_byte(dut, 0x5A)
    assert int(dut.empty.value) == 0

    # mid-byte: 3 bits in, then reset.
    dut.cs_n.value = 0
    await settle(dut)
    for i in range(3):
        dut.mosi.value = 1
        await settle(dut)
        dut.sclk.value = 1
        await settle(dut)
        dut.sclk.value = 0
        await settle(dut)

    dut.rst.value = 1
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    dut.cs_n.value = 1
    await RisingEdge(dut.clk)

    assert int(dut.empty.value) == 1, "reset must clear the queued byte too"
    assert int(dut.full.value) == 0

    # the shift/bit-count state must also be clear: a fresh full byte right
    # after reset must land correctly, not continue a stale partial count.
    await spi_send_byte(dut, 0xC3)
    got = await pop_byte(dut)
    assert got == 0xC3, f"bit count survived reset: popped {got:#x}"
