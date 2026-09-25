# sim_engine_error_* (netlist_lint, sim_tt, sim_pvt, mc)

ngspice printed a known failure signature: `singular_matrix`,
`gmin_stepping_failed`, `source_stepping_failed`, `timestep_too_small`,
`convergence_trouble`, `too_many_iterations`, `no_such_device`,
`no_such_node`, `unknown_subckt`, `unknown_model`, `empty_netlist`,
`simulation_interrupted`, `ngspice_error`, `sim_timeout`. The run's numbers are not
trusted, whatever the exit code said. Routes to `netlist`.

**By family:**
- *Convergence* (singular matrix, gmin/source stepping, timestep,
  trouble with node, iterations): a floating or DC-undefined node first,
  then a device biased far outside its rating. Don't add `.option`s to
  force convergence; a node with no DC path is a design bug.
- *Names* (unknown subckt/model, no such device/node, empty netlist): the
  bench instantiates something the netlist does not declare, or the pin
  count differs. Compare the bench's `x` line with `.subckt` in
  `netlist/<block>.cir` and `spec/topology.md`. If the bench is wrong,
  say so in OPEN - the bench-writer owns `tb/`.
- `{{...}}` left in a deck: a token `simlib.materialize` does not know.
- *Timeout* (`sim_timeout`): the run hit its time limit (60 s unless the
  bench asks for more), so nothing it printed is scored. A long sweep (a
  256-code DAC bench) needs a `* sim_timeout_s: <seconds>` line in its
  tb/*.cir, up to 3600; that is the bench-writer's to add, so say so in
  OPEN. A small bench that times out is hanging, and the cause is usually
  a convergence one above.

**Trap:** a `uic` cold start hides a bad operating point (docs/spikes/
dcosim.md). Let `.tran` compute the DC point first.
