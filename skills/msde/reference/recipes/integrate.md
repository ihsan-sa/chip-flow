# integrate - the two sides into one tile, and the checks on the join

Integrate is where the two nested runs meet. Both have already released on
their own; this verb joins them and checks the join. Almost all of the
joining is done by a gate, `top_harden`, not by an agent.

## Role

The integrator (`skills/msde/agents/integrator.md`) checks that the two
sides can be joined by name and writes the cosim bench. It writes no macro
config and no top netlist: `top_harden` generates both from the two
released sides.

## Inputs

- `interface.yaml` - which signals cross, and in which direction.
- The digital side's `spec/spec.yaml` ports and the analog side's
  `.subckt` line - read for names, never edited.
- Both sides released: `attest.py verify` valid on `{ws}/digital` and
  `{ws}/analog`.
- `brief/` - the top-level measures `cosim` checks.

## Outputs

1. The names check, in the integrator's SUMMARY.
2. The cosim bench under `{ws}/tb/`: `cosim_bench.json` (top, bounds,
   analog netlist, analog kind), the Verilog top, the analog model the
   bench simulates, `test_*.py`, and a `*.bounds.json` per measure set.
   `corpus/msde/ring_osc_div/tb/` is the working example.

## Contract

- Preconditions: `split` fresh-pass here; both nested workspaces verify as
  released.
- After the integrator returns: `cosim`, then `top_harden` as a detached
  job (`jobs.py start --gate top_harden --workspace {ws} --skill msde`,
  ten minutes or more, polled with `jobs.py status --workspace {ws}
  --all`), then `top_drc`, `top_lvs` and `precheck` on the GDS it produced.
- The integrator edits only `{ws}/tb/`. A name mismatch or a defect inside
  either side goes back to that side's own router as a finding.
- A side re-released after a fix needs nothing declared here:
  `top_harden` hashes `digital/rtl` and `analog/layout`, and `top_drc` and
  `top_lvs` hash its GDS, so the stale map re-runs them. A change to the
  digital spec or the analog sizing alone is not hashed - re-run
  `top_harden` by hand after one.

## Mechanics

`top_harden` (`engine/scripts/check_top_harden.py`) rebuilds `{ws}/top/`
from scratch on every run:

- `top/spec/spec.yaml` - the digital spec, minus the `tt_pins` entry of
  every `interface.yaml` signal, plus a `macros:` entry binding each of
  those signals to the analog pin of the same name
  (`engine/lib/ttlib.py`'s `macros` schema). The analog cell's two other
  pins go to VPWR (the one like `vdd`) and VGND (the one like `vss`/`gnd`).
- `top/rtl/` - a copy of `digital/rtl`.
- `top/macros/` - the analog cell's GDS (rebuilt from
  `analog/layout/gen_<block>.py`), a LEF abstract magic writes from it, a
  blackbox Verilog stub, and its sized `.subckt` for LVS.

The macro is placed in the middle of the tile, and the top is hardened by
the digital side's own harden. The macro GDS is re-saved without
klayout's context-info cell (`check_top_harden.clean_gds`), which magic
would otherwise read as a second top cell - nothing for the agent to do.

`top_harden` refuses (exit 2), naming the problem, when a nested workspace
is missing, when an interface signal is not both a digital port and an
analog pin, or when the analog cell's remaining pins are not one supply
and one ground. A macro larger than the tile is a `macro_too_large`
finding, and a `ua_pins` map off the analog template (a gap, a pad used
twice, too many pads) is a `ua_pin_off_template` finding; neither runs a
harden. Every one of those is a side's fix.
`docs/spikes/macro_harden.md` has how the recipe was found.
