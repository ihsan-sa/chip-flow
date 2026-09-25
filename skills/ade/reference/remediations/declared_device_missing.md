# declared_device_missing (netlist_lint)

spec.yaml's `devices` names a refdes the netlist does not instantiate.
Routes to `netlist`.

**Cheapest fix first:** check the spelling on both sides - the netlist's
`x` prefix is part of the refdes (`xmout`, not `mout`). Then decide which
side is right: a topology change that dropped the device is a spec edit
(the spec-writer updates `devices`), and a device the designer forgot is a
netlist edit.

**Trap:** a bias source lives in the bench, not the netlist, and
`netlist_lint` only reads `netlist/`. Don't move a bench source into the
netlist to satisfy this.
