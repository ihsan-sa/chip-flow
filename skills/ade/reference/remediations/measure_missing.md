# measure_missing (pex_sim)

The post-layout bench (`layout_ref/<block>_pex_tb.cir`) never printed a
value for a name its own `.bounds.json`'s `measures` map declares. Routes
to `testbench` (the bench-writer) - the bench is what is broken, not the
layout. Distinct from `sim_measure_missing` (the sim_tt/sim_pvt kind for
`tb/`'s bench) - same failure shape, different file, different bench.

**Cheapest fix first:** check the bench's own `.control`/`.measure`/
`print` lines against the bounds file's key names - a name mismatch
(case, a typo, a stale name from a spec revision) is the common cause.
`check_pex_sim.py` lower-cases every printed name before matching, so a
case difference alone is not the fault; a genuinely absent `.measure`/
`print` line, or one that measures the wrong node, is.

**Trap:** never delete the bound to make the finding go away - a measure
`post_layout_bounds` (or the spec's fallback) asks for has to actually be
scored, or the pex_sim gate is proving nothing for it.
