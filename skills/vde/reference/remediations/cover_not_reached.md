# cover_not_reached (formal)

A labeled `cover` point in `formal/*.sv` was never reached within the
spec's own depth. Routes to `formal`.

**False positive class:** a cover point that is genuinely unreachable
because the property-writer named a state the design is not actually
required to visit (a misread of the spec). Check the requirement's text
first.

**Cheapest fix first:** if the state IS reachable, the design likely needs
more depth to get there, or the RTL has a real defect preventing it from
ever reaching that state - check both before assuming one.

**Trap:** removing an inconvenient cover point rather than fixing the
reason it is unreached defeats the entire purpose of having it; that
decision, if it is ever genuinely correct, belongs to a human, not a
fixer.
