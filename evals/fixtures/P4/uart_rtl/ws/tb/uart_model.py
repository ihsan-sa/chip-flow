"""Cycle-level reference model of the uart transmitter, from spec/spec.md.

Not a test module (cocotblib.test_modules only collects test_*.py); the
tests in this directory import it.

Timing convention (spec.md "## Architecture"): tx and busy are registered.
A start sampled high on rising edge N, while idle and with rst low, latches
data on that edge; the outputs seen after edge N (and so sampled by the
next edge, N+1 - "the first clock of the frame") are busy=1, tx=start bit.
busy reads 1 for exactly 11*CLKS_PER_BIT clocks and 0 again afterwards.
rst sampled high on an edge wins over everything: idle after that edge.
A start sampled while a frame is in flight is ignored outright.

The model is stepped once per rising edge with the inputs the DUT samples
on that edge; `outputs()` then gives the tx/busy the DUT must show until
the following edge.
"""

FRAME_BITS = 11
DEFAULT_CLKS_PER_BIT = 4


def parity_even(byte: int) -> int:
    """XOR of the eight data bits: the bit that makes the nine bits
    data[7:0] + parity contain an even number of ones."""
    return bin(byte & 0xFF).count("1") & 1


def frame_bits(byte: int) -> list[int]:
    """The 11 line bits of one frame, in send order: start (0), data[0]
    .. data[7] (LSB first), even parity, stop (1)."""
    byte &= 0xFF
    return [0] + [(byte >> i) & 1 for i in range(8)] + [parity_even(byte), 1]


def decode_frame(line_bits: list[int]) -> dict:
    """Inverse of frame_bits, for readable failure messages."""
    data = 0
    for i in range(8):
        data |= (line_bits[1 + i] & 1) << i
    return {"start": line_bits[0], "data": data, "parity": line_bits[9],
            "stop": line_bits[10]}


class UartModel:
    def __init__(self, clks_per_bit: int = DEFAULT_CLKS_PER_BIT):
        assert clks_per_bit >= 1
        self.cpb = clks_per_bit
        self.frame_len = FRAME_BITS * clks_per_bit
        self.reset_state()
        self.frames_started: list[int] = []   # bytes accepted, in order

    def reset_state(self):
        self.active = False
        self.count = 0          # clocks into the current frame, 0-based
        self.bits = [1] * FRAME_BITS

    def step(self, rst: int, start: int, data: int):
        """Advance one rising edge with the inputs sampled on it."""
        if rst:
            self.reset_state()
            return
        if self.active:
            self.count += 1
            if self.count >= self.frame_len:
                self.reset_state()
            return          # a start while busy is ignored, not queued
        if start:
            self.active = True
            self.count = 0
            self.bits = frame_bits(data)
            self.frames_started.append(data & 0xFF)

    def outputs(self) -> tuple[int, int]:
        """(tx, busy) the DUT must show after the last stepped edge."""
        if not self.active:
            return 1, 0
        return self.bits[self.count // self.cpb], 1

    def where(self) -> str:
        """Human-readable position, for failure messages."""
        if not self.active:
            return "idle"
        k = self.count // self.cpb
        name = {0: "start bit", 9: "parity bit", 10: "stop bit"}.get(
            k, f"data[{k - 1}]")
        return (f"frame clock {self.count} ({name}, hold clock "
                f"{self.count % self.cpb} of {self.cpb})")
