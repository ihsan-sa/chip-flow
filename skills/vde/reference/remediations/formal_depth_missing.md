# formal_depth_missing (formal)

`spec/spec.yaml` has no `formal: {depth: N}`, so the formal gate ran
nothing and failed. The gate never picks a depth for you. Routes to
`formal` (the property-writer); a spec written before the depth was
required lands here the first time `formal` runs.

**Cheapest fix first:** add `formal: {depth: N}` with N the induction
depth the asserts need - usually small, a handful of cycles past reset.
k-induction proves unboundedly; it does not have to walk a long window.
If a cover needs a long sequence (a measurement window, a full frame, a
counter wrap), add `formal: {cover_depth: M}` (M >= N) set to at least
that sequence's cycles plus the cycles out of reset. Under
`multiclock` both count clock edges, about twice the cycles.

**Trap:** a deep `depth` to be safe. Each smtbmc basecase step costs
seconds, so a depth near a 1000-cycle window never finishes. A shallow
depth hides nothing: an assert whose induction does not close is
reported bounded, never proven.

**Edit only the `formal:` key** of `spec/spec.yaml`, nothing else there.
