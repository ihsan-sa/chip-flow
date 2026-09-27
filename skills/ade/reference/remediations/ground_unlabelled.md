# ground_unlabelled (pex_sim)

`netlist/<block>.cir` ties devices to ngspice's global ground (node `0`,
or `gnd`) without a port for it, and the magic extraction of the layout
has no node `0`: the layout's ground came back as a local net with a
magic-made name (`w_...#`, `a_...#`), so a bench on it runs with ground
floating. The gate refuses to guess which extracted net is ground and runs
no bench. Routes to `layout` (the layout-fixer).

**Fix:** in `layout/gen_<block>.py`, add a text label `"0"` on a shape of
the ground net, on the metal's drawing layer (metal1 is
`layoutlib.GF180_LAYER["metal1"]`, 34/0), passed in `finalize()`'s labels
list after the pins. Magic then names the net `0` without making it a
port. If the generator sorts its labels into pin order, keep the non-pin
label last (e.g. `PINS.index(l[0]) if l[0] in PINS else 99`). Don't open a
corpus rung's own `layout/` for this: that is the rung's answer key.

**Trap:** never put the `0` label on a pin/label layer
(`metal1_label`, 34/10): that makes it a port, the pin set no longer
matches the netlist's `.subckt` line, and pex_sim refuses. Never add a
ground pin to `netlist/` to make this go away - the layout follows the
netlist. Label the net that really is ground (the one LVS matched to `0`),
not the substrate of some other device.
