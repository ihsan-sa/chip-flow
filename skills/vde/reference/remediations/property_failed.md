# property_failed (formal)

SymbiYosys's k-induction (smtbmc) found a real counterexample for a
labeled property. Routes to `rtl` - formal is the answer to tests that
pass too easily (`docs/design.md` section 2); a property written fresh
from the spec, by an agent that never saw the RTL, finding a
counterexample is almost always a real design defect sim's own finite
test set missed.

**False positive class:** a property that is stricter than the spec
actually requires (the property-writer over-constrained it). Real, but
check the RTL against the spec's own text first - if the RTL genuinely
violates the requirement as written, fix the RTL; only report the
property as wrong in OPEN if you are confident the SPEC, not the design,
is what the property misread.

**Cheapest fix first:** sby's own counterexample trace (read the task's
log, not just the pass/fail) shows the exact cycle and signal values that
violate the assertion - reproduce it by hand against the RTL before
changing anything.

**Trap:** "fixing" this by loosening the property (widening what counts as
compliant) instead of the RTL defeats the entire gate - never do this to
make the gate pass; if the property is genuinely wrong, that is a decision
for a human, not a fixer's unilateral edit.
