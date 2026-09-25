# bad_corners (spec_lint)

A `corners` field (top level, or on one measure) is neither `default`,
`all`, nor a non-empty list of corner names - or, at top level only, a
`{grid: {process, temp_c, supply_pct}}` that `corners.grid_corners` refuses
(the finding's message names the reason).

**Cheapest fix first:** leave it out (it means `default`, the five
curated points) or list names `corners.py --names <n>` accepts.

**Trap:** a list never narrows the set. `check_sim_pvt.py` unions it with
the default five, so a spec can't skip `ss` by not naming it. Use the
`add-corner` verb to widen it. A grid replaces the five, so it must still
hold typical, ss and ff and the -40 and 125 C extremes; temperatures and
supplies in between (25 C) are fine.
