# requirement_no_check (spec_lint)

A requirement has no valid `check` kind (`sim`, `formal`, `both`, or
`measure`) - it names a behavior with no stated way to verify it. This is
the fault `spec_lint` exists to catch (`docs/design.md` 1.5).

Not in `cluster_violations.FIXER_HINTS` (falls back to `review`): a
`spec_lint` failure is normally handled directly by the spec-writer or
architect agent re-running their own pass, not through the generic
fix-finding loop - there is no `rtl`/`tb`/`formal` file to edit here, only
`spec/spec.yaml`.

**Fix:** pick the right `check` kind for the requirement (see
`skills/vde/agents/spec-writer.md`'s own guidance on choosing one) and add
it; if `measure`, add `bounds` too (`requirement_measure_no_bounds` is the
sibling finding for that specific gap).
