# setup_violation (timing)

OpenSTA found negative worst setup slack in at least one corner after
hardening. Routes to `harden`.

**Cheapest fix first:** the finding names the corner and the worst slack
value; timing/timing_work's own per-corner `.sta_<corner>.tcl` and log
carry the actual critical path. A tight but plausible RTL clock period in
spec.yaml is the first thing to check against harden/config.json's own
clock period - the two must agree, and a spec period the hardened design
genuinely cannot meet is a real design/floorplan tradeoff, not a tooling
bug.

**Trap:** do not "fix" this by loosening spec.yaml's clock period to
whatever the hardened design happens to achieve - that hides a real
performance shortfall instead of fixing it; only relax it when the spec's
own requirement (not just this run) genuinely allows it.
