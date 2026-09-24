# survivor_conditional_stuck (mutate)

A mutant forced a signal to a constant only under some condition (mcy's
`cnot0`/`cnot1` modes) and still passed the whole visible suite. Routes to
`testbench`.

**Cheapest fix first:** identify which condition the mutant targets (the
gate's own `mutation` string names the wire and mode) and add a test that
distinguishes the signal's real value from the stuck one specifically
while that condition holds.
