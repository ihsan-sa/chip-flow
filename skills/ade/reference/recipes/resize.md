# resize - change a device's size and re-prove it

One sizing change, by the analog-designer, then every gate sizing feeds.

## Where the number lives

A value in `sizing/sizing.yaml` is a `.param` the bench's `{{SIZING}}`
line sets, so a resize is an edit there, inside that entry's own
`min`/`max`. A device with no sizing entry is sized in `netlist/`; that
edit is a `netlist_edit`, not a `sizing_edit`, and it re-runs every analog
gate, including `netlist_lint`. Declare the class that matches the file
that actually moved.

## Why never sim_tt alone

The fault `sim_pvt` exists for is "meets at typical, loses headroom at slow
and hot". A resize that fixes typical can move the `ss`/125 C corner out of
bounds, so both run. `bench_strength` follows, because a resize can also
push the design into a region the bench no longer tells apart from a
mutant.

## Once a layout exists

`sizing_edit` marks `lvs` and `pex_sim` stale. The generator's own device
sizes must follow: that is a layout-fixer work order against
`layout/gen_<block>.py`. Never change `netlist/` back to match an old
layout.

## When many devices move together

Use `optimise` instead. A hand search over several coupled sizes is what
`optimise.py numeric` does better, against a frozen evaluator.
