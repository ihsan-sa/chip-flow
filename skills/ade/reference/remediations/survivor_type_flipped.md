# survivor_type_flipped (bench_strength)

An nfet swapped for a pfet (or the reverse) at the same voltage rating,
and every measure stayed in bound. A bench that can't see a device change
polarity measures almost nothing. Routes to `testbench`.

**Cheapest fix first:** the bounds may be one-sided or huge. The bench's
`min`/`max` must equal the spec's, so copy both sides the spec gives. If
they already match, a measure that moved but stayed inside the spec gets
a `sensitivity` on its bound, max(3 sigma, 2%) of its mc spread (2% when
no sigma is known).

**Trap:** don't add a measure that only exists to catch this mutant. Make
the bench measure what the spec promises, and the mutant fails on its own.
A bench may not score a measure the spec doesn't declare.
