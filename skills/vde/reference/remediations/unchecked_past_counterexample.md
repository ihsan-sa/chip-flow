# unchecked_past_counterexample (formal)

Info severity - never fails the gate by itself. Another property in the
same formal run has a real counterexample (`property_failed`). smtbmc's
basecase stops at the first step where any assert fails, and abc pdr checks
every assert at once and reports that same failure, so this property was
checked only up to that step, with no pdr corroboration. It is recorded as
bounded, never as proven, and it is not an `engine_disagreement`. Routes to
`formal` if it is ever dispatched.

**What to do:** nothing about this property. Fix the `property_failed` one
(the counterexample's own triage), then re-run formal: this property gets
its real verdict on that run. Don't rewrite it to make this finding go away.
