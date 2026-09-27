# device_undeclared (bench_strength)

The netlist has an element that spec.yaml's `devices` does not list, so
no mutant of it ran and the kill tally does not cover it. It is a
warning: it does not fail the gate. The report's `devices_mutated` of
`devices_total` shows how much of the netlist the tally covers.

**Cheapest fix first:** if the device matters to a measure, add it to
`devices` (a spec change - say so under OPEN) so the next run mutates
it. If it is deliberately out of scope, write why in spec.md, as
bandgap's spec.md does for its startup network.

**Trap:** don't read a clean kill tally as whole-netlist coverage while
this warning stands.
