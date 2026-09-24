# combinational_loop (synth)

yosys found a path with no register anywhere in a feedback loop -
synthesizable hardware cannot implement this; it is always a real RTL
defect, never a synth tool quirk. Routes to `rtl` (fixed in `rtl/`, never
by hand-editing the netlist synth produced).

**Cheapest fix first:** the finding names the module; trace every signal
in a suspected combinational `always @*`/`assign` chain back to its own
inputs - the loop is almost always a signal that (directly or through one
intermediate wire) feeds back into an expression that helps compute
itself, usually from a copy-paste or a refactor that dropped a register
stage.

**Trap:** this often passes `lint` cleanly (verilator's own combinational-
loop detection is weaker than yosys's synthesis-time analysis) - do not
treat a clean lint pass as evidence the RTL has no loop.
