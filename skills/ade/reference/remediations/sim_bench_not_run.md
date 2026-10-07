# sim_bench_not_run (sim_tt, sim_pvt)

Every bound in the bench's `.bounds.json` sidecar has a `corners` list, and
none of those lists names any corner of the spec's sweep, by any of its
names (`tt` is also `tt_27c`), so no gate ever runs the bench. A bench
skipped at a corner is fine (the report's `not_scored` lists each), and so
is one sim_tt skips because only another corner of the sweep scores it (its
`out_of_scope` lists each; sim_pvt runs it). The same finding on `tb/` means
no bench at all was scored at this run's corners, so the gate scored
nothing. Routes to `testbench`.

**Fix:** scope at least one bound to a corner in the sweep (its spec
measure's `corners`, or "all"), or, if the bench really belongs to a corner
the set leaves out, add that corner to the spec's `corners`.

**Trap:** don't delete the bench or its sidecar to make the finding go away;
its measures then go unchecked everywhere.
