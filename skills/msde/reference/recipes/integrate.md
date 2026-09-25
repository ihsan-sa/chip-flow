# integrate - the analog block into the tile, and the checks on the join

Integrate is where the two nested runs meet. One role, the integrator,
writes all three joining artifacts so they cannot disagree with each other.

## Role

The integrator (`skills/msde/agents/integrator.md`) turns a released analog
layout and a synthesized digital side into one tile: the analog block
enters the digital side's harden as a LibreLane hard macro, so the whole
Tiny Tapeout tile's GDS comes out of ONE harden.

## Inputs

- `interface.yaml` - which pins cross, and in which direction.
- The analog side's released layout: `analog/layout/<analog block>.gds` and
  the abstract `layout_gen.py` writes beside it.
- The digital side's harden config: `digital/harden/config.json` and
  `digital/harden/info.yaml`.
- The digital side's RTL top and the analog side's netlist - read for pin
  names, never edited.

## Outputs

1. The macro entry in the digital side's harden config.
2. The top netlist, with the analog block as a subcircuit - what `top_lvs`
   compares the assembled GDS against.
3. The cosim bench under `{ws}/tb/`: `cosim_bench.json` (top, bounds,
   analog netlist, analog kind), the Verilog top, the analog model the
   bench simulates, `test_*.py`, and a `*.bounds.json` per measure set.
   `corpus/msde/ring_osc_div/tb/` is the working example.

## Contract

- Preconditions: `split` fresh-pass here; `attest.py verify --workspace
  {ws}/analog` valid (the analog side has released the GDS being used).
- After the integrator returns: `cosim` first, then `state.py edit --class
  harden_config_edit` in the DIGITAL workspace, then its harden job, its
  signoff gates and its own release through `task_router.py --skill vde
  --verb resume --workspace {ws}/digital`, then `top_drc` and `top_lvs`
  here.
- The integrator edits only the digital harden config, the top netlist and
  `{ws}/tb/`. A mismatch it finds inside either side's design goes back to
  that side's own router as a finding; the integrator never edits
  `digital/rtl/`, `analog/netlist/` or any generated GDS.
- A macro whose GDS changes (the analog side re-released) means
  integrate runs again: the digital harden is stale the moment its macro
  moves, whatever its own hashes say.

## Mechanics

See docs/spikes/macro_harden.md.
