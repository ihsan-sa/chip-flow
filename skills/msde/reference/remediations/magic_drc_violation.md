# magic_drc_violation (top_drc)

magic's DRC deck found a violation in the assembled top's GDS
(`top/harden/runs/run/final/gds`) - the standard cells and the analog
macro's geometry in one layout.

**Cheapest fix first:** find where it sits. The report under
`top/log/drc_work/` gives layer and location; compare it with the macro's
placement in `top/spec/spec.yaml` (`macros[].location` and its LEF size).
Inside the macro, it is an analog layout fix: through `analog/`'s own
router (its layout code, its own `drc` gate) to a fresh release, then
`top_harden` again. At the macro's edge, where routing meets its pins,
check the pin geometry in the analog layout first.

**Trap:** the analog side's own `drc` passing does not clear this - the
macro sits beside routing it never saw. Never hand-edit the GDS.
