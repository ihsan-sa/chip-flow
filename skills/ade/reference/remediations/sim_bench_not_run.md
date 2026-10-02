# sim_bench_not_run (sim_tt, sim_pvt)

Every bound in the bench's `.bounds.json` sidecar has a `corners` list, and
none of those lists names a corner this run sweeps, so the bench was never
run. A bench skipped at a corner is fine (the report's `not_scored` lists
each); a bench skipped at every corner means the gate scored nothing for it.
Routes to `testbench`.

**Fix:** scope at least one bound to a corner in the sweep (its spec
measure's `corners`, or "all"), or, if the bench really belongs to a corner
the set leaves out, add that corner to the spec's `corners`.

**Trap:** don't delete the bench or its sidecar to make the finding go away;
its measures then go unchecked everywhere.
