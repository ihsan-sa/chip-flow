# passive_corner_unselected (sim_pvt)

The netlist uses a poly/diffusion resistor or a MIM cap, the corner sweep
moves that device off typical (a passive corner, or ss/ff), and the bench
hard-codes the typical passive section (`.lib ... res_typical` or
`mimcap_typical`) instead of `{{RES_CORNER}}` / `{{MIM_CORNER}}`. Every
passive corner would then simulate at typical and pass unseen. Routes to
`testbench`.

**Fix:** replace the hard-coded line with
`.lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' {{RES_CORNER}}` (or
`{{MIM_CORNER}}`); sim_run fills in res_<p> / mimcap_<p> per corner.

**Trap:** don't drop the device's `.lib` line or the corner to make the
finding go away. The resistor and MIM spread (about +/-20% and +/-10-15%)
is what those corners exist to check.

**When the person ruled it out:** if the approved H1 answer (or the note
recorded with it) says that device's spread is out of scope, record that
ruling from their words instead of touching the bench:
`state.py scope-out --workspace <ws> --dimension mim_cap --quote '<their
sentence, verbatim>'` (`resistor` for the resistor). sim_pvt then pins
only that device at typical, sweeps everything else, and no longer asks
for the placeholder; the release record and the design document say it
was scoped out. state.py refuses a quote that is not in the recorded
answer or does not name that device, so a session cannot scope one out
the person did not.
