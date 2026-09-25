# level_mismatch (split)

A signal's `level` (e.g. `cmos_3v3`) differs between `interface.yaml` and
a side spec. The receiver expects a swing the driver does not produce.

**Cheapest fix first:** the level the analog block can really produce or
accept decides it. Correct the disagreeing file. If the analog side cannot
reach the digital logic level, the brief needs a level shifter on one
side - a design choice to record, not a spelling fix.

**Trap:** a level string that only differs in spelling (`cmos3v3` vs
`cmos_3v3`) still fails; `split` compares values exactly. Use the spelling
`interface.yaml` uses.
