# survivor_reset_removed (mutate)

A mutant that removed the reset's effect on some register still passed
the whole visible suite. Must-kill class - a single survivor here fails
the gate regardless of the overall kill rate. Routes to `testbench`.

**Cheapest fix first:** add (or strengthen) a test that checks the
specific register's value immediately after reset is asserted, not just
after the design has been running a while - a suite that only checks
post-reset behavior indirectly, several cycles later, is exactly what this
mutant class slips through.

**Trap:** checking reset only at time zero (before the design has ever
run) misses a mutant that removes reset's effect ONLY when reasserted
mid-run - cover both.
