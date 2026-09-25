"""Shared drive/sample helpers for counter8 tests.

Everything is FallingEdge-anchored: inputs change on a falling edge, outputs
are sampled on the falling edge after the rising edge that updated them.
"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from counter8_model import Counter8Model

PERIOD_NS = 10


def read_count(dut, what="count"):
    v = dut.count.value
    assert v.is_resolvable, f"{what}: count is not resolvable (got {v!s})"
    return v.to_unsigned()


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.model = Counter8Model()
        self.cycle = 0

    async def start(self):
        self.dut.rst.value = 1
        cocotb.start_soon(Clock(self.dut.clk, PERIOD_NS, unit="ns").start())
        await FallingEdge(self.dut.clk)

    async def tick(self, rst: int, check: bool = True):
        """Drive rst for the next rising edge, then sample on the following
        falling edge and compare against the model."""
        self.dut.rst.value = rst
        await FallingEdge(self.dut.clk)
        self.model.step(rst)
        self.cycle += 1
        got = read_count(self.dut, f"cycle {self.cycle}") if self.model.known else None
        if check and self.model.known:
            assert got == self.model.count, (
                f"cycle {self.cycle}: rst={rst} expected count={self.model.count} "
                f"got count={got}")
        return got

    async def reset(self, cycles: int = 2):
        for _ in range(cycles):
            got = await self.tick(1)
            assert got == 0, f"during reset: expected count=0 got {got}"
