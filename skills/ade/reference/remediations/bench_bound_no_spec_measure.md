# bench_bound_no_spec_measure (spec_lint)

A bench bounds a measure spec.yaml never declares: an undeclared
requirement enforced on every run, which nobody reading the spec sees.

**Cheapest fix first:** if it is a real requirement, the spec-writer adds
it to `measures` with the same bounds. If it is a debugging probe, the
bench-writer removes its bound (a `.measure` with no bound is harmless).

**Trap:** matching names only. A spec `iout` and a bench `iout_raw` are
two different measures to this check - rename one side so they agree.
