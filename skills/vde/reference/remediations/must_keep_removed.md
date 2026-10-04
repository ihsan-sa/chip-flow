# must_keep_removed (synth)

spec.yaml's `must_keep` names an instance or net (a hand-placed delay
cell, a ring oscillator's stage) that no cell or net of the flattened
netlist carries any more. Synth flattens the design, so a name inside a
submodule is found as `<instance>.<name>`; this finding means it is
really gone, not renamed. Routes to `rtl`.

**Cheapest fix first:** mark the cell instance and the nets it drives
`(* keep *)`, and make sure its output is read by something, so `opt`
cannot remove it. If the name in `must_keep` is misspelt, fix the spec.

**Trap:** do not drop the name from `must_keep` to get past this. The
list is what the optimiser and this gate both hold the design to.
