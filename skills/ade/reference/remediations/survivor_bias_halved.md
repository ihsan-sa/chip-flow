# survivor_bias_halved (bench_strength)

A bias source in the bench (one listed in spec.yaml's `devices`) was
halved and no measure moved out of bound. Routes to `testbench`.

**Cheapest fix first:** a current mirror's output tracks its reference
one to one, so a bound that survives a halved reference is wider than the
mirror ratio tolerance. Tighten the output bound, or add a measure of the
ratio itself.

**Trap:** removing the source from `devices` to spare it from mutation
hides the weakness instead of fixing it.
