# measure_no_bounds (spec_lint)

A measure in spec.yaml has no `bounds` with a `min` or a `max`. This is the
planted fault `spec_lint` must catch: a requirement nothing can fail.

**Cheapest fix first:** take the bound from the brief's own numbers. A
brief that says "about 24 uA" needs a tolerance the person can accept -
ask at the next checkpoint rather than invent one.

**Trap:** a bound so wide it can't fail (`min: 0, max: 1e9`) passes this
gate and then fails `bench_strength`, because no device mutant can move
the measure outside it. Write the bound the block actually has to meet.
