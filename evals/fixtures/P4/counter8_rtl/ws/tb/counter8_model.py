"""Cycle reference model of counter8, from spec/spec.yaml alone.

One step() is one rising edge of clk: rst high at the edge -> 0 (REQ-RST-SYNC),
otherwise previous value + 1 modulo 256 (REQ-INC, REQ-WRAP). Before the first
reset the value is unknown (spec.md: power-on value unspecified), held as None.
"""


class Counter8Model:
    WIDTH = 8
    MOD = 1 << WIDTH

    def __init__(self):
        self.count = None  # unknown until the first reset edge

    def step(self, rst: int) -> None:
        if rst:
            self.count = 0
        elif self.count is not None:
            self.count = (self.count + 1) % self.MOD

    @property
    def known(self) -> bool:
        return self.count is not None
