# dac_spi

An 8-bit DAC whose code is loaded over SPI into a register on the digital
side (docs/design.md section 3's `/msde` corpus list, "### M10.": "the DAC
with an SPI register ... join as specs with faults and may stay red").

**This rung is a spec with faults only.** There is no reference design yet
- no digital RTL, no analog netlist, no nested `/vde`/`/ade` workspaces -
so only `split` runs here (docs/design.md 1.5 msde table). Its `ladder.md`
row may stay red until a later milestone gives it a reference design and
wires up `cosim`, `top_drc`, `top_lvs` and `release`.

## What the block does

The digital side is an SPI slave (`sclk`, `mosi`, `cs_n`, all TT pins)
shifting in an 8-bit word while `cs_n` is low and, on its rising edge,
latching the shifted byte into an 8-bit code register. `dac_en` gates
whether that register's value reaches the analog side at all (power-down
between updates). The analog side is an 8-bit R2R ladder DAC followed by
an output buffer, driving `vout` on an analog pin.

## Crossing signals

| signal   | direction | level     | domain   | width |
|----------|-----------|-----------|----------|-------|
| code     | d2a       | cmos_3v3  | clk_sys  | 8     |
| dac_en   | d2a       | cmos_3v3  | clk_sys  | 1     |

## Top-level measures (a future cosim bench)

Once a reference design exists, a `cosim` bench would check, at gf180
3.3 V supply:

- `code = 0x00` -> `vout` within 50 mV of 0 V.
- `code = 0x80` -> `vout` within 50 mV of 1.65 V (mid-scale).
- `code = 0xFF` -> `vout` within 50 mV of 3.28 V (full scale, `255/256 *
  3.3 V`).
- Monotonic: `vout(code+1) >= vout(code)` for every consecutive code pair,
  swept `0x00` through `0xFF`.
- `dac_en = 0` holds `vout` at its last latched value (power-down does not
  reset the ladder).

None of these run yet - there is no bench to run them in. They are
recorded here as the contract a reference design would have to meet.
