# bad_split_devices (spec_lint)

A `split_devices` entry is not exactly `{refdes, cell, why}`, its `cell` is
not a GF180 standard cell (`gf180mcu_fd_sc_mcu7t5v0__*` or
`gf180mcu_fd_sc_mcu9t5v0__*`), or its `refdes` is missing from `devices`.

**Cheapest fix first:** copy the entries from the analog brief's
`## Split devices` section as the /msde splitter wrote them, and add each
refdes to `devices`.

**Trap:** this list is the only way a device the topology template lacks
gets into the netlist, and it is for the split's standard-cell drivers
alone. A transistor or resistor the template lacks is a topology question
for the person, not an entry here.
