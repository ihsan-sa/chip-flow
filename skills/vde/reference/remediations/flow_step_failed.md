# flow_step_failed (harden)

LibreLane exited non-zero mid-flow (routing failed to complete, a step
died, the design does not fit the tile). Not a launcher failure (that
case, no flow log at all, refuses the gate outright instead) - this is a
real flow attempt that got partway and stopped. Routes to `harden`.

**Cheapest fix first:** the finding's message carries `error.log`'s own
tail, or the flow's last 2000 characters of output when there is no
error.log - read it before touching anything; LibreLane usually names the
step and the specific complaint (congestion, an unroutable net, a macro
that does not fit the floorplan) directly.

**Trap:** "design too large for the tile" is a floorplan concern first
(utilization - `PL_TARGET_DENSITY_PCT` in `harden/config.override.json`;
die area and core margins are the tile's and the override refuses them),
not an RTL one -
do not start cutting logic out of the design before checking whether a
floorplan knob fixes it first.
