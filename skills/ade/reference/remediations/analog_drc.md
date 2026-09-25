# analog_drc (drc) - every klayout GF180 rule family, by check name

klayout's GF180 signoff deck names rules as an open set (`M1.2a`,
`DF.14_LV`, `metal1_OFFGRID`, ...) - no per-rule file can enumerate them
all, so `fix_dispatch.py`'s remediation lookup falls back to this one file,
keyed by the gate's own tool name (`analog_drc`), whenever a rule-specific
file does not exist (`skills/ade/SKILL.md`: "every klayout DRC rule lands
on analog_drc.md"). Routes to `layout` (the layout-fixer).

magic DRC is not run for this gate at all - the spike
(`docs/spikes/glayout.md`) found magic silently re-snapping a GDS to the
manufacturing grid on load, hiding an off-grid shape klayout's own deck
reports honestly. klayout's result is the one that counts; do not treat a
clean magic run as evidence of anything.

**Cheapest fix first, by rule family (the finding's `file` carries the
layer name via `layoutlib.drc_layer_of()`, `module` the cell):**
- `*_OFFGRID` - a shape not on the 0.005um manufacturing grid. Snap the
  coordinates the generator computed (an arithmetic offset, a non-integer
  micron step) before `layoutlib.finalize()`'s own snap runs.
- A spacing rule (e.g. `M1.2a`, `MET1.5`) - two shapes on the same layer
  too close together. Move the routing, don't shrink a device to make room.
- A `DF.*` guard-ring/tap rule - a well or source region with no tap
  within the PDK's own distance. Add `layoutlib.psub_tap()`/`nwell_tap()`
  near the offending region (see `corpus/ade/mirror/layout/gen_mirror.py`'s
  own tap placement for the pattern).

**Trap:** never widen a spacing in the generator "just to be safe" without
reading which rule it fixes - a shape moved to satisfy one rule can create
a new violation on an adjacent layer. Re-run `drc` after every change; do
not batch several rule fixes on faith.
