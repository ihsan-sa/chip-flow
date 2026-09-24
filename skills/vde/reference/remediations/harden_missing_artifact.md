# harden_missing_artifact (harden)

LibreLane exited 0 but `final/` is missing one of the expected output
formats (GDS, netlist, def, ...) - a flow that reported success without
actually producing everything downstream gates (drc/lvs/timing/glsim)
need. Routes to `harden`.

**Cheapest fix first:** re-run the harden job (`jobs.py start --gate
harden`) once cleanly before assuming anything is wrong - a step can be
skipped by a stale LibreLane run-dir reused across a restart. If it
recurs, check harden/config.json's own output format list against what
the flow's config actually asks LibreLane to emit.

**Trap:** do not treat exit 0 as proof the run is usable - this finding
exists precisely because LibreLane's own exit code does not guarantee
every artifact landed.
