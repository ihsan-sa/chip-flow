# sim_measure_missing (sim_tt, sim_pvt, mc)

A bounded measure never printed a value: the `.measure` trigger/target was
never met ("failed!") or the name is missing from ngspice's output. Routes
to `testbench`, since the bench-writer owns the `.measure` line.

**Cheapest fix first:** a name mismatch between the `.measure` and the
`.bounds.json` entry, then a `FIND ... AT=` time past the end of `.tran`.

**False positive class:** a trigger that is never met because the design
never switches (an output stuck at a rail) is a design failure. When the
measure prints at some corners and not others, say so in OPEN and let the
orchestrator route it to `sizing`.

**Trap:** don't replace a crossing measure with a `FIND` at a fixed time
just so it always prints. That hides the failure it exists to catch.
