# survivor_condition_inverted (mutate)

A mutant that inverted a branch condition (an `if`, a comparison) still
passed the whole visible suite. Must-kill class. Routes to `testbench`.

**Cheapest fix first:** this almost always means the suite never actually
exercises BOTH sides of the condition with a distinguishing check on each
- add a test (or extend an existing one) that drives the input right up
against the condition's boundary on both sides and asserts the different
expected outcome on each.

**Trap:** a test that exercises both branches but only checks a shared
side-effect (both branches happen to leave some other signal in the same
state) will not kill this mutant - the assertion has to depend on WHICH
branch executed, not merely run after either one.
