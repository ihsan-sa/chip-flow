# cosim - both sides in one bench

`cosim` builds `{ws}/tb/`'s Verilog with Icarus and runs its cocotb tests,
with the analog block simulated by ngspice through cocotbext-ams's
`MixedSignalBridge`. So one bench drives both sides: the digital side
reacts to transitions ngspice actually computed.

It is not built on ngspice's `d_cosim`/`ivlng` bridge, which is broken in
this image. `docs/spikes/dcosim.md` says why and records the choice
`docs/design.md` section 5 asked for.

## What the bench must provide

- `tb/cosim_bench.json`: `top`, `bounds`, `analog_netlist`, `analog_kind`
  (`ideal` or `transistor`).
- A bench that writes `reports/cosim_measures.json` BEFORE any assertion
  can raise: every bounded measure, `digital_toggles` backed by that many
  `edge_times_ns`, and `spice_time_reached_ns`.

## What the gate does not trust

cocotb's own pass alone, and ngspice's silence. It scans the sim log for
non-convergence and for an ignored `.ic`, requires evidence the digital
side toggled and the analog side ran far enough, then checks every bound.
A failing cocotb test is reported as well, never instead.

## Routing a failure

The bench is the integrator's. A finding that traces into a side's design
(a divider that divides by the wrong ratio, a control word driven with the
wrong polarity) is fixed in that side's nested workspace through its own
router and gates, then `cosim` re-runs here. Never loosen a bound or edit
`reports/cosim_measures.json` to make it pass.

`cosim`'s recorded inputs are `interface.yaml` and `tb/`. Neither nested
workspace is hashed into it, so after a fix inside a side, re-run `cosim`
by hand.
