# survivor_bias_halved (bench_strength)

A bias source in the bench (one listed in spec.yaml's `devices`) was
halved, no measure moved out of bound and none moved past its declared
`sensitivity`. Routes to `testbench`.

**Cheapest fix first:** a current mirror's output tracks its reference
one to one, so a halved reference moves the output a long way. The
bench's `min`/`max` must equal the spec's; if they do, give the output's
bound a `sensitivity` in `tb/*.bounds.json`, max(3 sigma, 2%) of its mc
spread (2% when no sigma is known). If the spec has no measure the
source drives, that is a spec change: say so under OPEN.

**Trap:** removing the source from `devices` to spare it from mutation
hides the weakness instead of fixing it.
