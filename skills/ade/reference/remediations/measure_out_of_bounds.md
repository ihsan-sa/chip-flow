# measure_out_of_bounds (pex_sim)

A post-layout measure came out of its bound at typical, against the
magic-extracted parasitic netlist. Routes to `layout` (the layout-fixer) -
parasitics the routing itself added (a long, narrow output line's own R
and C, an under-sized via count) are the layout's fault, distinct from
`sim_bound_fail` (a pre-layout schematic miss, which routes to `sizing`).

**Cheapest fix first:** the finding names the measure and its bound; check
`layoutlib.count_parasitics()`'s R/C counts in the gate's own report for
which nets carry the most extracted parasitics. A long minimum-width
routing segment on a sensitive node (the mirror's own `plant_pex_
long_output_line.py` fault shape) is the common case - widen the trace or
shorten the run in `layout/gen_<block>.py`.

**Trap:** `pex_sim` runs typical only, magic's extractor only (SKILL.md's
Known limits) - a fix that only looks better at typical may still be tight
at a real corner; there is no gate here to catch that yet, so prefer a fix
with real margin over one that only just clears the bound.
