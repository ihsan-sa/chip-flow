# device_not_mutated (bench_strength)

spec.yaml's `devices` names a device the gate generated no mutant for, so
nothing tested whether the bench would notice it breaking. The device is
in the report's `unmutated` list with its reason and is not in the
killed tally. It fails the gate. Routes to `review`.

**Cheapest fix first:** read the reason.
- A B source whose expression runs onto a `+` line: join it onto one line.
- A B source whose expression's extent is ambiguous (a bare expression
  with spaces): wrap it in `'...'` or `{...}`.
- A bench source that is not a plain `i`/`v` with a numeric value (an SI
  suffix such as `10u`, a PWL): write the value as a plain number.
- A ref found nowhere: fix the spelling in spec.yaml or the netlist.
- An element kind no mutation class covers (an E/G/F/H controlled
  source, a primitive R/C/M line): either instantiate the PDK device
  instead, or have the spec-writer take it out of `devices` with the
  reason written in spec.md, as bandgap's spec.md does for its startup
  network. That is a spec change, so say so under OPEN.

**Trap:** don't drop a device from `devices` just to clear this finding.
Without a written reason, that hides an untested device.
