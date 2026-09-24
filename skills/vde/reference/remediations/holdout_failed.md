# holdout_failed (holdout)

A held-out test failed against the current RTL. The finding names only
the requirement id(s) and, by design, never the held-out test itself
(`docs/design.md` section 2) - work from the requirement id and the
VISIBLE tests that already cover it. Routes to `rtl`: this is a real
functional gap the visible suite did not catch, which is the entire
reason a held-out set exists.

**Do not** go looking for the held-out test file to see what it actually
checks - that defeats its purpose for every future edit to this block,
not just this one.

**Cheapest fix first:** re-read the requirement's full text in `spec.md`/
`spec.yaml`, not just its one-line summary, for a case the visible tests
happen not to exercise - a corner value, a sequence of operations, an
edge asserted mid-run rather than only at start-up. Fix the RTL against
the SPEC, not against a guess at what the hidden test wants.

**Trap:** fixing this by pattern-matching against the visible tests'
specific inputs (rather than the requirement's general statement) often
produces an RTL change that still fails a DIFFERENT held-out case next
time - fix the general behavior, not the one case you can see.
