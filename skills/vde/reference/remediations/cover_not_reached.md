# cover_not_reached (formal)

A labeled `cover` point in `formal/*.sv` was never reached within the
spec's own depth (`formal.cover_depth` if set, else `formal.depth` - the
finding names which). Routes to `formal`.

**False positive class:** a cover point that is genuinely unreachable
because the property-writer named a state the design is not actually
required to visit (a misread of the spec). Check the requirement's text
first.

**Cheapest fix first:** if the state IS reachable, the design likely needs
more depth to get there - raise `formal.cover_depth` (add it if absent) to
at least the cycles the sequence takes from reset - or the RTL has a real
defect preventing it from ever reaching that state - check both before
assuming one.

**Raise `cover_depth`, not `depth`.** `depth` is the prove depth, the k of
smtbmc's k-induction; it should stay at what the asserts need for
induction, usually small. Raising it to a long cover's reach makes the
prove task infeasible (each basecase step costs seconds, so ~1000 steps
never finish). Only the cover task reads `cover_depth`.

**Trap:** removing an inconvenient cover point rather than fixing the
reason it is unreached defeats the entire purpose of having it; that
decision, if it is ever genuinely correct, belongs to a human, not a
fixer.
