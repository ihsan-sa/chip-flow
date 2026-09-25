# survivor_size_doubled (bench_strength)

Doubling one device's W (or L) left every measure inside its bound. The
bench can't tell this design from a different one, so its bounds are too
wide or it measures the wrong thing. This is the planted `bench_strength`
fault. Routes to `testbench` (the bench-writer), never the designer.

**Cheapest fix first:** check the bound against the spec. A mirror's
output current moves with Wout/Wref, so a bound that absorbs a 2x change
is wider than any spec needs. Tighten it to the spec's own tolerance, or
measure the quantity the device actually sets.

**Trap:** don't resize the design so the mutant happens to fail. The
gate scores the bench.
