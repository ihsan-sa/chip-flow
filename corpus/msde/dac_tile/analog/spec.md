# dac_tile_analog - the analog half of dac_tile

The nested `/ade` block of the msde rung `dac_tile` (../spec.md): a 2-bit
R-2R DAC whose output leaves the chip on a Tiny Tapeout analog pad, not
through the digital side.

## What it does

Each bit input drives a CMOS inverter on gf180mcuD 3.3V devices
(`pfet_03v3` to `vdd`, `nfet_03v3` to `vss`, L=0.5um), and the inverter's
output drives that bit's leg of an R-2R ladder at the correct 2:1 ratio
(R 10um, 2R 20um, both 2um wide), with the ladder's foot on `vss`.

Because of the inverters, **vout is high for bits low**: code 00 gives the
top of the range and code 11 gives ground.

| bmsb blsb | vout at tt | over the default PVT set |
|-----------|------------|--------------------------|
| 0 0       | 2.85V      | 2.52-3.17V               |
| 0 1       | 2.19V      | 1.91-2.46V               |
| 1 0       | 0.59V      | 0.52-0.66V               |
| 1 1       | 0V         | 0V                       |

An ideal ladder reads 3/4, 7/12 and 1/6 of vdd at the first three codes;
the pfets' on-resistance costs 5-10% of that. Supply scaling is most of
the PVT spread, so the bounds in spec.yaml are the PVT envelope with a
little margin.

The ladder uses `ppolyf_u_1k` resistors (about 1k ohm/sq), not
corpus/ade/r2r_dac's `rm1`. That rung drives its ladder from ideal
sources, where rm1's half-ohm legs are fine; behind an inverter they are
swamped by the switch's on-resistance and vout stays under a millivolt at
every code.

## Interface

`dac_tile_analog(bmsb, blsb, vout, vdd, vss)`:

- `bmsb`, `blsb` are 3.3V CMOS inputs from the digital side
  (../interface.yaml).
- `vout` is the ladder's output, about 5k ohm of source resistance, and
  goes to the analog pad `ua[0]` (../interface.yaml `ua_pins`). The bench
  loads it with 1pF.
- `vdd`/`vss` are the 3.3V rails.

## Layout

layout/gen_dac_tile_analog.py, a 45.6 x 22.2um macro in sensor_counted's
pin conventions: `vdd`/`vss` metal3 straps the full cell width along the
top and bottom, `bmsb`/`blsb` metal2 tracks at the left edge and `vout`
one at the right edge. Nothing is drawn above metal3.
