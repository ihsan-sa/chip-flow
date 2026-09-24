# survivor_stuck_other (mutate)

A mutant stuck an internal (non-output, non-reset) signal at a constant
value and still passed the whole visible suite. Not a must-kill class by
itself, but it still counts against the overall kill rate. Routes to
`testbench`.

**Cheapest fix first:** an internal signal a stuck-at mutant can hide
behind is usually one nothing downstream depends on OBSERVABLY within the
visible suite's own test window - check whether the signal actually
affects a port within the cycles the suite runs for; if it genuinely
never does, this may be dead logic worth flagging in OPEN for the
rtl-writer instead of a testbench problem to chase.
