# survivor_other (mutate)

A mutant mcy's own classifier could not place in a more specific class,
and it still passed the whole visible suite. Routes to `testbench`.

**Cheapest fix first:** read the gate's own `mutation` string for this
survivor directly (it names the exact mcy edit) rather than guessing from
the generic kind alone, then add a test that distinguishes the mutated
behavior from the original.
