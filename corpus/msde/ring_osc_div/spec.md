# ring_osc_div

A ring oscillator with a divider (docs/design.md section 3's `/msde` corpus
list, harder than `sensor_counted`). The analog block is a free-running
five-stage ring oscillator; the digital side is a divide-by-2 flip-flop.
This rung exercises the `cosim` gate (docs/design.md 1.5 msde table): one
bench, `tb/test_ring_osc_div.py`, drives both sides through
cocotbext-ams's `MixedSignalBridge` and reports the divided frequency.

## Why this ring oscillator is RC/comparator behavioral, not gf180
transistor-level

docs/spikes/dcosim.md (M10's cosim spike) found and fixed a real bug in an
early attempt at a gf180 transistor-level two-inverter block under this
bridge (a `.ic` statement that silently no-op'd because it named a node
that does not exist at the netlist's top level - see that doc), and proved
the bridge itself converges cleanly on that fixed case for a bounded run
(docs/spikes/dcosim/run.sh, section 3). A five-stage ACTIVE transistor ring
oscillator is a substantially harder cold-start problem than one pair of
gates sitting at a fixed input (an unforced symmetric equilibrium that must
be pushed off balance to start swinging at all, on top of the same
BSIM-under-`uic` sensitivity the two-inverter case needed real effort to
resolve) - solving that is layered on top of, not required by, this gate's
own pass criterion ("every top-level measure inside its bound").

So this rung's `ring5.sp` is a real SPICE feedback oscillator - five RC-lag
stages, each an ideal threshold comparator (`B` sources), wired in a ring -
computed by ngspice exactly like any other analog block, just built from
devices with none of BSIM's near-zero-leakage/cold-start sensitivity
(linear R/C time constants only). Staggered `.ic` values across the five
stages break the symmetric equilibrium so it starts swinging immediately
under `uic`, the same startup technique a real current-starved ring would
still need. `skills/ade/reference/topologies/` (M8, in flight) is expected
to carry an actual gf180 current-starved ring topology for `/ade` designs
proper - swapping this rung's `ring5.sp` for that one, once it exists, is
future work the `cosim` gate itself does not depend on either way.

## Bench shape

- `tb/ring5.sp`: the 5-stage ring, `.subckt ring5 osc_out vdd vss`.
- `tb/ring_osc_div_top.v`: `ring_osc_div_top` (a bare `osc_out` net the
  bridge forces) instantiating `clk_div2`, a real divide-by-2 flip-flop.
- `tb/test_ring_osc_div.py`: drives `ring5` through cocotbext-ams, counts 8
  real `clk_div` edges, records `divided_freq_hz` and `digital_toggles` to
  `reports/cosim_measures.json`.
- `tb/cosim_bench.json` / `tb/ring_osc_div.bounds.json`: the top module name
  and the measure's bound, read by `engine/scripts/check_cosim.py`.
