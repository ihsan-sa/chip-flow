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

Real simulated values on this box (gf180mcuD, typical unless named): vref
1.19V, vref_line_pp ~4mV, vref_tc_pp ~4mV - all inside spec.yaml's bounds
([1.16, 1.21]V, 15mV, 6mV).

## Layout

docs/design.md "### M9." names this rung's row as one that "may stay red at
layout" - no `layout/` generator, and no `drc`/`lvs`/`pex_sim`/`release`
entries in `faults/manifest.yaml`; see that file's own header comment for
why. This spec covers only what `spec.yaml` checks pre-layout: `spec_lint`,
`netlist_lint`, `sim_tt`, `sim_pvt`, `bench_strength`.
