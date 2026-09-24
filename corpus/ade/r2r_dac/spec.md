# r2r_dac - 2-bit R2R ladder DAC

The second rung of `/ade`'s corpus (docs/design.md section 3, "### M8."):
a passive block whose whole design correctness rests on a resistor RATIO,
and the corpus rung this milestone's optimise loop (section 4) is written
against.

## What it does

A 2-bit R2R resistor ladder: `bmsb` (nearest the output through a single R
leg) and `blsb` (one more R+2R stage removed) drive the ladder, and `vout`
reads the resulting divided voltage - measured empirically on this design
(unloaded, no output buffer): MSB alone gives ~2.4V, LSB alone ~0.6V, both
~3.0V, at VDD=3.3V and the correct 2:1 R:2R ratio.

## Interface

`r2r_dac(bmsb, blsb, vout, r_unit=..., r2_unit=...)` - `r_unit`/`r2_unit`
are the R and 2R leg values, a plain ngspice `R` element each (not this
PDK's `rm1` resistor subcircuit - see netlist/r2r_dac.cir's own comment on
why: `rm1`'s corner-dependent sheet resistance did not resolve inside a
further-nested subcircuit call on this box, and this rung's own point - a
wrong RATIO breaking the transfer function, then the sizing loop fixing it
- only needs a real, tunable resistor VALUE, not this PDK's physical model).

## Requirement, and this rung's own twist

`vout_lsb`/`vout_msb`/`vout_both` (docs/design.md 4's per-measure bounds)
are all inside their bound ONLY at the correct 2:1 ratio. This rung ships
with `sizing/sizing.yaml` (and the netlist's own subckt defaults) set to
the WRONG 1:1 ratio on purpose - `sim_tt` is deliberately NOT in this
rung's `faults/manifest.yaml` (it is expected to fail on the untouched
reference, the same way a harder corpus rung's "row may stay red" until
its own later milestone lands, docs/design.md "### M6."/"### M9.") -
`engine/scripts/optimise.py numeric` against `sizing/sizing.yaml` is this
milestone's own done criterion for reaching every measure inside bounds
from that wrong start.
