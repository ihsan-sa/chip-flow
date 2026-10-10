# measure_bad_corners (spec_lint)

A `corners` field (top level, or on one measure) is neither `default`,
`all`, nor a non-empty list of corner names.

**Cheapest fix first:** leave it out (it means `default`, the five
curated points) or list names `corners.py --names <n>` accepts.

**Trap:** a list never narrows the set. `check_sim_pvt.py` unions it with
the default five, so a spec can't skip `ss` by not naming it. Use the
`add-corner` verb to widen it.

**Also:** a measure's own list must name corners the spec's sweep runs,
by any of their names (`tt_27c` is also `tt`). Under a `{grid: ...}` the
typical corners are `tt_<temp>c` (`tt_25c`), not `tt`, so a measure scoped
to `[tt]` would never be scored. The passive corners (`tt_pss`, `tt_pff`)
are accepted here because a resistor or MIM netlist adds them to any
sweep; sim_pvt refuses the bound (`sim_bound_not_swept`) if this netlist
does not.
