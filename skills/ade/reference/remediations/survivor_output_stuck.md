# survivor_output_stuck (bench_strength)

A behavioural `b...` source listed in spec.yaml's `devices` had its
expression replaced by 0, and no measure moved out of bound or past its
declared `sensitivity`. Routes to `testbench`.

**Cheapest fix first:** find the measure the source's output drives - a
comparator stage's output sets a frequency or a count, an ideal buffer's
sets a level. If the bench measures it only where the source already
sits at 0 (an operating point taken before the stage switches), measure
where the output is non-zero. The bench's `min`/`max` must equal the
spec's; if a stuck output moves the measure but stays inside the spec,
give it a `sensitivity` in `tb/*.bounds.json`.

**Trap:** removing the source from `devices` makes the gate warn
`device_undeclared` instead, which hides the weakness rather than fixing
it.
