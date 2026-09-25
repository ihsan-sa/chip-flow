# survivor_type_flipped (bench_strength)

An nfet swapped for a pfet (or the reverse) at the same voltage rating,
and every measure stayed in bound. A bench that can't see a device change
polarity measures almost nothing. Routes to `testbench`.

**Cheapest fix first:** the bounds are almost certainly one-sided or huge.
Bound both sides of every measure the spec gives a value for.

**Trap:** don't add a measure that only exists to catch this mutant. Make
the bench measure what the spec promises, and the mutant fails on its own.
