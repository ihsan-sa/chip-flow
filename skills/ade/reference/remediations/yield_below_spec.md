# yield_below_spec (mc)

The Monte Carlo yield (samples with every measure in bound) is below
`mc.yield_min`. Routes to `sizing`.

**Cheapest fix first:** mismatch shrinks with device area. Larger W*L on
the matched pair (a mirror, a differential input) is the textbook fix,
before any topology change. Read which measure fails in the failing
samples: it points at the matched pair.

**Trap:** lowering `yield_min` or `runs` is a spec change for the person.
A higher `runs` count does not raise yield; it only tightens the estimate.
