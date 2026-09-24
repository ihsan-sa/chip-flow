# survivor_output_stuck (mutate)

A mutant that stuck an output port at a constant 0 or 1 still passed the
whole visible suite. Must-kill class. Routes to `testbench`.

**Cheapest fix first:** a suite where this survives usually never asserts
the SPECIFIC output value on some input combination - it may check "the
output changed" or "the output is nonzero" without pinning the exact
expected value. Add an assertion that checks the precise expected value,
not just its presence or its direction of change.
