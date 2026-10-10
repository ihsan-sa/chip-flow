# sim_bound_not_swept (sim_pvt)

A bench ran, but one of its bounds is scored only at corners the sweep
never runs, by any of their names, so that bound would go unscored while
the gate passed. spec_lint lets a measure name a passive corner (`tt_pss`,
`tt_pff`) without reading the netlist; sim_pvt sweeps those only when the
netlist uses a poly/diffusion resistor or a MIM cap whose spread was not
scoped out at H1. Routes to `testbench`.

**Fix:** scope the measure, in the spec and the bench's sidecar alike, to a
corner this sweep runs (or "all"), or name the corner in the spec's
`corners` so it is swept.

**Trap:** don't drop the bound, or scope it to "all" when it only holds at
one corner; a bound that is never scored checks nothing.
