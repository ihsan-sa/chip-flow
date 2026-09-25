# netlist_mismatch (top_lvs)

netgen found the assembled top's GDS does not match the powered netlist
with the analog `.subckt` added. The macro is compared device by device,
so a mismatch can sit in the standard cells, in the wiring to the macro,
or inside the macro itself.

**Cheapest fix first:** the finding carries netgen's tail, and the full
report is at `log/top_lvs/top_lvs.rpt`. An unmatched net at a macro pin
(the corpus fault: a macro pin left unconnected) usually means a pin in
the analog layout that is missing, misnamed or off its label: an analog
layout fix through `analog/`'s own router, then `top_harden` again.
Devices or properties off inside the macro mean the layout and the sized
`.subckt` disagree - run the analog side's own `lvs` first.

**Trap:** LVS is a fabrication-correctness gate. Never waive it without a
person's decision, and never edit `top/macros/` to make it match.
