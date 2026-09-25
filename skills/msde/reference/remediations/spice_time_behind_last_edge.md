# spice_time_behind_last_edge (cosim)

The analog side's recorded time stops before the last digital edge the
bench measured. The digital side claims edges the analog side never
reached.

**Cheapest fix first:** the bench's analog run is shorter than its digital
wait, or the edges come from a source other than the bridge. Lengthen the
analog `tran` to cover the whole measurement window, or take the edges
from the bridged signal.

**Trap:** do not trim the edge list to fit - the edges the analog side
never produced are the finding.
