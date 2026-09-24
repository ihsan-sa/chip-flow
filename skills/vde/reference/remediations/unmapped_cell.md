# unmapped_cell (synth)

yosys's synthesized netlist still contains a `$`-prefixed internal cell
type that never got mapped to a gf180mcu standard cell - synthesis is
incomplete, not just imperfect; this netlist cannot be hardened. Routes to
`rtl`.

**Cheapest fix first:** this is almost always a Verilog construct the
liberty's own cell library has no direct mapping for (an odd-width
arithmetic operator, a memory-like construct without an explicit memory
model, a construct that needs a library yosys's `techmap`/`dfflibmap`/
`abc` steps do not cover for this PDK) - simplify the construct to
something closer to a plain register/mux/logic-gate description rather
than relying on inference for something unusual.

**Trap:** the unmapped cell's name (e.g. `$mem`, `$alu`, `$fa`) tells you
WHAT kind of construct is unmapped even when the finding's own file/line
is unhelpful - use it to search the RTL for the matching Verilog
construct.
