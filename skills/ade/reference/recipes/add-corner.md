# add-corner - widen a block's PVT set

`corners.py --names <corner>` confirms the name exists in
`engine/reference/corners.yaml`. Then the spec-writer (or you, for a
one-line list edit) adds it to spec.yaml's own `corners` list, and
`spec_edit` declares it.

## A list only ever adds

`check_sim_pvt.py` unions a spec's `corners` list with the default five
(typical plus the four extremes). A spec cannot drop `ss` by leaving it out,
so this verb can only widen the set, never narrow it.

## Never edit corners.yaml per block

It is shared by every block. A corner that is not in it yet is a change to
the engine's reference, reviewed on its own, not a side effect of one
block's run.

## What to expect

`spec_edit` marks every ade gate stale, so after `sim_pvt` the remaining
gates auto-schedule. A new corner that fails is a normal fix-loop finding
(`sim_bound_fail` at that corner). Widening the bound to absorb it is a
spec decision for the person, not a fix.
