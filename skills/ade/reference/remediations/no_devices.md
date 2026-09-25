# no_devices (spec_lint)

spec.yaml has no non-empty `devices` list. `netlist_lint` checks every
refdes on it is instantiated, and `bench_strength` mutates exactly these
devices, so an empty list leaves the bench unscored.

**Cheapest fix first:** the analog-designer fills it at P2 from
`spec/topology.md` - every transistor and resistor the netlist will carry,
as the `x<name>` refdes the netlist will use. A bias source the bench
declares (`iref_src`) may be listed too: `bench_strength` halves it.

**Trap:** leaving a device off the list to spare it from mutation hides
exactly the device a weak bench cannot see.
