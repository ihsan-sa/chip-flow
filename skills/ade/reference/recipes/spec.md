# spec - write or revise spec.yaml/spec.md

A fresh spec-writer pass, or a deliberate revision on a block that already
has a netlist, benches and passing gates.

## Why the cascade is not a bug

`spec_edit` marks every ade gate stale (`invalidation.yaml`), and that is
correct. A bound, the supply, the corner set or a `post_layout_bounds`
entry can move, and every gate reads spec.yaml somewhere: `netlist_lint`
reads `devices`, `sim_pvt` reads `corners` and `supply`, `mc` reads `mc`,
and `spec_lint` cross-checks the measures against the benches. Run the full
mapped set, not the gates this edit "probably" touches.

## What this verb does not do

It does not rewrite `netlist/`, `tb/` or the layout generator. A changed
bound shows up as `measure_no_bench_bound` or `bench_bound_no_spec_measure`
on the next `spec_lint` until the bench-writer brings `tb/` back in line,
and a tightened bound shows up as a `sim_*` failure for the fix loop.

## Tightening a bound is not loosening one

Widening a bound to make a failing block pass is a spec change the person
owns (`human_hold` 2 on `spec_edit`). Say so at the next checkpoint and
never make it inside a fix loop.
