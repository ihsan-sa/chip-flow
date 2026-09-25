# measure_no_bench_bound (spec_lint)

A spec measure has no bound in any `tb/*.bounds.json`. The spec claims it,
but no bench scores it at any corner. It only appears once `tb/` exists.

Routes to `review`: either the bench-writer missed it (spawn it with this
finding) or the spec-writer added it after the benches were written.

**Cheapest fix first:** the bench-writer adds a `.measure` and a bounds
entry with the spec's own numbers.

**Trap:** deleting the measure from spec.yaml to clear this drops a
requirement. That is a spec change for the person, not a fix.
