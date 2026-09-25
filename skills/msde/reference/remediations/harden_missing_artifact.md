# harden_missing_artifact (top_harden)

LibreLane exited 0 on the assembled top, but its `final/` is missing an
output (GDS, LEF, netlist, SDF, metrics) that `top_drc` and `top_lvs`
need. `top_harden` passes on `check_harden`'s finding.

**Cheapest fix first:** re-run the job once (`jobs.py start --gate
top_harden --workspace <ws> --skill msde`) - `top_harden` rebuilds `top/`
from scratch, so a stale run directory cannot survive it. If it recurs,
the digital side's own harden config is where the output list lives; fix
it there, through `digital/`'s own router, and re-release that side.

**Trap:** a zero exit is not a finished flow. Never copy an artifact into
`top/harden/` by hand.
