# property_failed (formal)

SymbiYosys's k-induction (smtbmc) found a counterexample for a labeled
property. Routes to `formal`, the property-writer, first. It reads the
counterexample against the spec, never the RTL, so it can tell a property
that misstates the spec from a design that breaks it. Sending this to
`rtl` first is worse: the rtl fixer cannot edit `formal/` and would be
pushed to bend correct RTL toward a wrong property.

**Triage against the spec first.** Read sby's own counterexample trace
(the task's log, not just pass/fail) for the exact cycle and signal values
that violate the assertion, then check the property's claim against the
spec's own text.

- The property misstates the spec (over-constrained, wrong cycle, a
  harness off-by-one): fix `formal/*.sv` so it says what the spec says.
- The property states the spec correctly: leave it alone and say so in
  OPEN. The orchestrator then re-dispatches the finding to `rtl`, where a
  counterexample from a property written fresh from the spec, by an agent
  that never saw the RTL, is a real design defect sim's finite test set
  missed (`docs/design.md` section 2).

**Trap:** "fixing" this by loosening the property (widening what counts as
compliant) to make the gate pass defeats the entire gate. Fix a property
only to match the spec; if the spec itself is what looks wrong, that is a
decision for a human.
