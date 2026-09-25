# bandgap - self-biased delta-VBE bandgap reference

The third rung of `/ade`'s corpus (docs/design.md section 3, "### M9."):
the first block whose row may stay red at layout - see "Layout" below.

## What it does

A self-biased delta-VBE bandgap reference. `xq1`/`xq2` (with `xq2` run at
`m=8`, i.e. eight unit devices in parallel) and `xr1` set up a PTAT
(proportional-to-absolute-temperature) current through the core mirror
(`xm1`-`xm4`); that current is mirrored into the output branch (`xm5`) and
forced through `xr2` and `xq3`, summing a CTAT (complementary-to-absolute-
temperature) `Vbe` with a PTAT drop across `xr2` to cancel each device's own
temperature slope at the output node `vref`. `xms1`-`xms3` are a startup
circuit that kicks the self-biased core out of its zero-current state on
power-up.

## Interface

`bandgap(vref, vdd, vss)` - `vref` is the reference output (a bench reads
its DC level, its move across the VDD line and its move across temperature),
`vdd`/`vss` are the supply rails. `r1_length`/`r2_length` are the two
`.subckt` parameters that size `xr1`/`xr2`; `r2_length` is also this rung's
`sizing/sizing.yaml` optimise target.

## Requirement

- `vref` sits at a real bandgap voltage after a power-up ramp from 0V,
  measured as `vref` (`FIND v(vref) AT=30u` on `tb/bandgap_tb.cir`'s PWL
  ramp) - near 0V would mean the startup circuit failed to kick the core out
  of its degenerate zero-current state.
- `vref` moves little as VDD sweeps 2.97V to 3.63V (+-10% of the 3.3V
  supply), measured as `vref_line_pp` (`PP v(vref) FROM=40u TO=60u`, the
  same bench's two VDD plateaus) - line regulation, the bandgap's headroom
  against a noisy or drooping supply rail.
- `vref` moves little from -40C to 125C, measured as `vref_tc_pp`
  (`tb/bandgap_tc_tb.cir`'s `.dc temp -40 125 5` sweep, `PP v(vref)`) - the
  temperature-coefficient cancellation the whole topology exists for; a
  first-order bandgap's residual curvature is what this bounds.

Real simulated values on this box (gf180mcuD, default corner set): vref
1.18011-1.19217V across tt/ss/ff/sf/fs, vref_line_pp 0.0101-0.0116V,
vref_tc_pp 0.00259-0.00317V - all comfortably inside spec.yaml's bounds
([1.16, 1.21]V, 15mV, 6mV).

## bench_strength scope

`spec.yaml`'s `devices` list holds `xm1`-`xm5`, `xr1`, `xr2`, `xq1`-`xq3`
accountable to `bench_strength`, not `xms1`-`xms3` (the startup network):
at typical corner `xq2`'s own `m=8` mismatch against `xq1` already biases
the self-biased core away from its degenerate zero-current point on its
own, confirmed even under a forced initial condition that starts every
node - including the startup devices' own gate/drain nodes - at a literal
0V/degenerate guess. No non-destructive mutant of `xms1`-`xms3` (bigger W,
a floated body - the terminal `bench_strength`'s mutants always target,
tied to each one's own source) moves any declared measure; only physically
removing one does, and that is not a mutant this gate ever plants. Real
silicon still needs the startup network for the process/mismatch corners
this deterministic sim does not sweep - that is `mc`'s job, and this rung
declares no `mc` block, the same "not applicable by default" carve-out
gates.yaml's own `ade.mc` row already documents for a spec with none.

## Layout

docs/design.md "### M9." names this rung's row as one that "may stay red at
layout" - no `layout/` generator, and no `drc`/`lvs`/`pex_sim`/`release`
entries in `faults/manifest.yaml`; see that file's own header comment for
why. This spec covers only what `spec.yaml` checks pre-layout: `spec_lint`,
`netlist_lint`, `sim_tt`, `sim_pvt`, `bench_strength`.
