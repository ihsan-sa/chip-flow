# survivor_gain_halved (bench_strength)

A behavioural `b...` source listed in spec.yaml's `devices` had its
expression multiplied by 0.5, and no measure moved out of bound or past
its declared `sensitivity`. Routes to `testbench`.

**Cheapest fix first:** a halved output only survives when the bench reads
it through a threshold that the half-size swing still crosses (a logic
level, a zero crossing). Measure the level itself, or the timing that
depends on it, and bound that at the spec's `min`/`max`; add a
`sensitivity` when the move stays inside the spec. If a threshold-only
reading is genuinely all the block promises, the owner can rule the
mutant `equivalent` in `spec/mutant_rulings.yaml`.

**Trap:** removing the source from `devices` makes the gate warn
`device_undeclared` instead, which hides the weakness rather than fixing
it.
