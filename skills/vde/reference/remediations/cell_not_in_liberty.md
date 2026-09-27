# cell_not_in_liberty (synth)

The RTL instantiates a standard cell by name (a delay cell, a clock
buffer kept on purpose) that the liberty synth read does not define. The
finding names the cell and the liberty. Synth refuses rather than pass a
netlist the chosen library cannot build. Routes to `rtl`.

**Cheapest fix first:** check which library the spec means. Synth reads
spec.yaml's `std_cell: {library, corner}`. With no such key, a TT target
(a spec with `tt_pins` or `tiles`) gets the TT GF template's library,
gf180mcu_fd_sc_mcu7t5v0 at tt_025C_3v30, and anything else gets
gf180mcu_fd_sc_mcu9t5v0 at tt_025C_5v00. If the spec names the wrong
library, fix the spec. If the library is right, instantiate a cell it has,
with the same prefix.

**Trap:** do not add a `(* blackbox *)` stub of the cell under
`ifdef SYNTHESIS` to get past this. Synth reads the liberty before the
RTL, so a cell the library has needs no stub. A stub of a cell it lacks
still fails here, because the stub survives into the netlist under a name
the liberty does not define.
