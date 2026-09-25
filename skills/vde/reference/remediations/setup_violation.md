# setup_violation (timing)

OpenSTA found negative worst setup slack in at least one corner after
hardening. Routes to `harden`.

**Cheapest fix first:** the finding names the corner and the worst slack
value; timing/timing_work's own per-corner `.sta_<corner>.tcl` and log
carry the actual critical path. A small miss (a fraction of a ns at the
slow corner) usually closes with LibreLane's own post-route resizer: write
`{"RUN_POST_GRT_RESIZER_TIMING": 1}` to `harden/config.override.json` (a
JSON object of LibreLane keys, merged last into the config harden
generates), declare `harden_config_edit`, and re-harden. Never edit
`harden/config.json` itself - harden regenerates it on every run and the
edit is lost. The override may not set CLOCK_PERIOD (that is spec.yaml's
`clock.period_ns`), tile/PDK keys or the template's DO-NOT-CHANGE keys;
harden refuses them with exit 2. A spec period the hardened design
genuinely cannot meet is a real design/floorplan tradeoff, not a tooling
bug - the next lever is the RTL.

**Trap:** do not "fix" this by loosening spec.yaml's clock period to
whatever the hardened design happens to achieve - that hides a real
performance shortfall instead of fixing it; only relax it when the spec's
own requirement (not just this run) genuinely allows it.
