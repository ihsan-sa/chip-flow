# model_not_in_pdk (netlist_lint)

A device names a model the PDK's own ngspice files do not declare. This is
the planted `netlist_lint` fault. Routes to `netlist`.

**Cheapest fix first:** use the gf180mcu_fd_pr subckt name exactly:
`nfet_03v3`, `pfet_03v3` (and the `_05v0`/`_06v0` ratings), `rm1` and the
other resistors, `cap_mim_2f0fF`. The list is read live from the toolchain
(`netlistlib.known_models()`), so a typo and a model from another PDK look
the same.

**Trap:** don't define a local `.model` or `.subckt` with the missing name
to make the check go quiet. The simulation would run on a device the fab
can't build, and LVS against the PDK's extraction would fail anyway.
