# sim_bound_fail (sim_tt, sim_pvt, mc)

A measure came out of its bench bound at a corner (the finding's refs are
`[measure, corner]`). The bench was written first, from the spec, so this
reads as the design missing the spec. Routes to `sizing`.

**Cheapest fix first:** find the corner pattern. Fails only at `ss`/125 C
or the low-supply corners: too little headroom (overdrive, a device near
the edge of saturation) - size for the worst corner, not typical. Fails at
every corner by a similar ratio: a wrong mirror ratio or a W/L swap. Use
the topology header's design equations, then `sizing.yaml` inside its
bounds; several coupled sizes are `optimise` work.

**False positive class:** a bound in absolute volts on a quantity that
scales with VDD fails the +/-10% supply corners however good the sizing
is (the r2r_dac rung today). That is a spec/bench problem - report it in
OPEN, never widen the bound yourself.

**Trap:** editing `tb/*.bounds.json` is never a sizing fix.
