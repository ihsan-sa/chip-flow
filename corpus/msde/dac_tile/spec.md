# dac_tile

A 2-bit DAC as a Tiny Tapeout *analog* tile. The digital side registers a
2-bit code from `ui_in`; the analog side converts it with an R-2R ladder
behind CMOS reference switches. The analog output `vout` leaves the chip
on the analog pad `ua[0]`, not through the digital side, which is what
makes this rung the test case for an analog tile.

The digital side is a nested `/vde` block (digital/) and the analog side a
nested `/ade` block (analog/). `digital_spec.yaml` / `analog_spec.yaml`
are the two sides' declared copies of the crossing signals, which the
`split` gate checks against `interface.yaml`.

`interface.yaml` also carries `ua_pins`, the analog-cell pins brought out
to the tile's analog pads. Only `check_top_harden` is meant to read it,
and that engine work has not landed yet, so faults/manifest.yaml is empty
until the top gates can place an analog pad.

Note that vout is high for bits low (analog/spec.md).

## Crossing signals

| signal | direction | level    | domain  | width |
|--------|-----------|----------|---------|-------|
| bmsb   | d2a       | cmos_3v3 | clk_sys | 1     |
| blsb   | d2a       | cmos_3v3 | clk_sys | 1     |

## Analog pads

| analog pin | pad    |
|------------|--------|
| vout       | ua[0]  |
