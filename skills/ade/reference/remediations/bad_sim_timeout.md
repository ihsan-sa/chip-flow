# bad_sim_timeout (spec_lint)

`sim_timeout_s` in spec.yaml is not a mapping of gate name to seconds, names
a gate that runs no ngspice, or holds a limit outside (0, 3600]. Every
ngspice gate would refuse it too (exit 2), never fall back to its default.

**Cheapest fix first:** `sim_timeout_s: {pex_sim: 600}`, naming only the gate
whose bench is slow. The gates it may name are `netlist_lint`, `sim_tt`,
`sim_pvt`, `mc`, `bench_strength` and `pex_sim`. A gate left out keeps its
default (30 s for netlist_lint, 120 s for pex_sim, 60 s for the rest).

**Trap:** a limit is not a fix for a bench that hangs. Raise it only when the
run finishes by hand inside the new limit, as a large RC netlist does.
