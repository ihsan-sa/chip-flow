# no_measures (spec_lint)

spec.yaml has no `measures` list at all - `measure_no_bounds` at its most
extreme. An analog block with nothing measured has nothing to pass.

**Cheapest fix first:** one entry per quantitative promise in the brief
(an output current, a gain, a delay, an offset), each with bounds.

**Trap:** don't add a placeholder measure to get past the gate. A measure
no bench computes fails `spec_lint` again as `measure_no_bench_bound` the
moment `tb/` exists.
