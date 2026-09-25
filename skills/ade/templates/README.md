# templates

Starter shapes, not generated boilerplate - an agent copies and edits one
of these, never runs a generator over it. Real, working examples live in
`corpus/ade/*` (read those first for house style, especially
`corpus/ade/mirror/` and `corpus/ade/r2r_dac/`); this is the bare skeleton
when a corpus rung isn't a close enough match.

- `spec.yaml.template` - the minimum `spec_lint` needs: supply, devices,
  corners, one measure with bounds. See `skills/ade/agents/spec-writer.md`
  for how to pick a measure and its bounds, and
  `engine/lib/speclib.py`'s `lint_spec_ade` for the full schema.

- `step_settle_tb.cir` - the settling-time half of a step bench: its
  window sized from the expected settling, and a `not_settled` verdict
  (a `sim_not_settled` finding) instead of a number clamped to the
  window. See `skills/ade/agents/bench-writer.md`.

Two sidecar shapes an agent has to get right and this template does not
cover directly (they differ from each other - see
`corpus/ade/mirror/tb/mirror_tb.bounds.json` vs
`corpus/ade/mirror/layout_ref/mirror_pex_tb.bounds.json`):

- `tb/<block>_tb.bounds.json` - a LIST of `{"measure", "min"?, "max"?,
  "severity"?, "msg"?}` (`engine/lib/simlib.py`'s `load_bounds()`), used by
  `sim_tt`/`sim_pvt`.
- `layout_ref/<block>_pex_tb.bounds.json` - a DICT `{"measures": {<name>:
  {"min"?, "max"?}}}`, read directly by `check_pex_sim.py`.

There is no netlist or layout-generator template here on purpose - a real
topology in `skills/ade/reference/topologies/*.sp` (design equations and
sizing bounds) plus a corpus rung's own `netlist/*.cir` and
`layout/gen_*.py` are the pattern every block follows; a bare skeleton for
either would only invite copying constants that do not fit the block being
built.
