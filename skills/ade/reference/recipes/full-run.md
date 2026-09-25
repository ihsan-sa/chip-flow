# full-run - spec.md to a released analog package

The whole analog pipeline, P1 through P6, as one recipe. Every phase either
writes an artifact through an agent or checks one through a gate, and the
run reaches `release` only by running every gate it owes.

## Before you start

Derive a short snake_case block name (`mirror`, `r2r_dac`, `comparator` -
it becomes `netlist/<block>.cir`, `layout/gen_<block>.py` and the pex
bench's file name)
and pass `--workspace blocks/<name>` to the router. Copy the spec file(s)
the task named into `{ws}/brief/` verbatim. If the brief is a corpus rung's
`spec.md`, copy that file alone: the rung's `spec.yaml`, `netlist/`,
`sizing/`, `tb/`, `layout/` and `layout_ref/` are its answer key.

## P1-P2: spec, then topology

The spec-writer turns the brief into `spec/spec.md` and `spec/spec.yaml`
(supply, measures with bounds, corners, `post_layout_bounds`, `mc` only when
the brief asks for a yield). The analog-designer in TOPOLOGY MODE picks a
template from `skills/ade/reference/topologies/`, writes
`spec/topology.md` (the subckt name, its pin order, every device refdes,
the design equations it will size against) and fills spec.yaml's
`devices`. `spec_lint` runs after each.

## P3: benches before the netlist exists

The bench-writer reads `spec/` only - spec.md, spec.yaml, topology.md - in
fresh context, and writes `tb/<block>_tb.cir` plus its `.bounds.json`.
It never sees a netlist, because none exists yet. Re-run `spec_lint`: with
`tb/` present it also checks every spec measure has a bench bound and every
bench bound is a spec measure.

## P4: netlist, sizing and the simulation gates

The analog-designer in NETLIST MODE writes `netlist/<block>.cir` and
`sizing/sizing.yaml`. Run the five P4 gates in order - `netlist_lint`,
`sim_tt`, `sim_pvt`, `bench_strength`, `mc` - and let the fix loop take
each failure before the next gate runs. A corner miss that sizing can
close is often cheaper through the `optimise` verb than by hand;
`bench_strength` survivors go to the bench-writer, never the designer.

`mc` with no `mc.enabled: true` in spec.yaml answers "not applicable" and
exits 2 without recording a result (SKILL.md, Known limits). That is the
one gate this recipe may leave unrecorded, and only with a `state.py
decision` that quotes the gate's own `applicable: false`.

## H1

Spawn a fresh reviewer once P4 is green. Present its digest with the gate
table, worst margin per measure first, and record `state.py human
--checkpoint H1`.

## P5: layout

The bench-writer, in PEX MODE, writes `layout_ref/<block>_pex_tb.cir` and
its bounds from `post_layout_bounds` before any layout exists. The
layout-writer then writes `layout/gen_<block>.py`. `layout_gen.py` must
run clean before `drc`, `lvs` and `pex_sim`. Their findings go to the
layout-fixer, who edits the generator and never the GDS.

## H2, then P6 release

A second fresh reviewer, then H2. `set-phase P6` without `--force` is the
proof that drc, lvs and pex_sim each have a recorded result. Then `release`
(strict), `attest.py build`, `attest.py disposition`, the P6 digest, and
`set-phase done`. If `release` refuses, it lists every gap at once: go back
to the phase that owns each one. Never force a phase to get past it.
