# digital_toggles_unbacked (cosim)

`digital_toggles` is set, but `edge_times_ns` does not hold that many
recorded edges. A toggle count must be evidence, not a literal.

**Cheapest fix first:** make the bench append each observed edge time to
`edge_times_ns` and derive `digital_toggles` from its length.

**Trap:** this is the bench claiming a result it did not measure. Never
pad the list.
