# sensor_counted_analog - the analog half of sensor_counted

The nested `/ade` block of the msde rung `sensor_counted` (../spec.md): a
ring oscillator whose output the digital side counts over a gate window,
with an enable the digital side uses to stop it between measurements.

## What it does

A five-stage ring on gf180mcuD 3.3V devices (`nfet_03v3`/`pfet_03v3`).
Stage 0 is a NAND of the ring's last node and `osc_en`, stages 1-4 are
inverters, and a short-channel inverter buffers the ring into `osc_out`.
Every ring device is L=10um: a long-channel stage delay goes as L^2, so the
ring runs at a few MHz with no capacitor and no bias circuit.

## Interface

`sensor_counted_analog(osc_en, osc_out, vdd, vss)`, the order of
../analog_spec.yaml's contract:

- `osc_en` high runs the ring; low stops it, with `osc_out` held low.
- `osc_out` is a full-swing 3.3V CMOS output able to drive a standard-cell
  input.
- `vdd`/`vss` are the 3.3V rails.

## Requirement

The digital side samples `osc_out` with a 50MHz clock, so `f_osc` stays
under 12.5MHz at every corner, and above 1MHz. Simulated on this box:
4.84MHz typical, 2.93 (ss, 125C, -10%), 7.43 (ff, -40C, +10%), 3.25 (sf),
6.85 (fs); 4.53MHz typical after extraction (layout_ref/).

## Bench strength

Frequency alone cannot tell a resized device apart: PVT moves it 2.5x, far
more than one device at twice its width. So the bench also measures each
stage's share of the ring period, rise delay plus fall delay. Process skew
moves a stage's two delays in opposite directions and temperature and
supply scale the whole period, so each share stays within about 0.001 over
the five default corners (0.007 for the NAND stage and stage 4, which the
buffer loads). A ring device at twice its width moves a share by 0.02-0.06;
one with its bulk left floating moves it by 0.0025-0.006, which is why the
bounds are only 0.0012 outside the PVT envelope for stages 1-3.

`devices` in spec.yaml lists the ten ring devices. Four are left out,
because no measure can bound them across PVT: the enable pfet `xmp0e`
(off while the ring runs, so its width changes nothing), the enable
nfet `xmn0e` (its floating-bulk mutant moves nothing), and the buffer
`xmpb`/`xmnb` (their width only moves `osc_out`'s edges, by less than PVT
does). A type flip of any of the four stops the ring, and LVS checks their
sizes against the netlist.

## Layout

layout/gen_sensor_counted_analog.py, a 45.6 x 31.0um macro in the pin
conventions of docs/spikes/macro_harden/gen_inv.py: `vdd`/`vss` metal3
straps the full cell width along the top and bottom, `osc_en` a metal2
track at the left edge and `osc_out` one at the right edge. Nothing is
drawn above metal3.
