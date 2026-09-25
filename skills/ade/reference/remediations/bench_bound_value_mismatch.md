# bench_bound_value_mismatch

A `tb/*.bounds.json` entry scores a different `min` or `max` than the
spec measure it implements. spec.yaml is the contract, so the bench moves
to match it; never edit the spec to match the bench.

No fixer domain owns `tb/`. Triage it to the bench-writer in WORK-ORDER
MODE, which brings the sidecar's `min`/`max` to the spec's `bounds`. It
usually follows a `spec` revision that moved a bound, so check the latest
`spec_edit` note first.

**Trap:** a bench tighter than the spec looks safe but fails a block that
meets its contract, so fix that direction too.
