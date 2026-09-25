# ngspice_non_convergence (cosim)

The sim log carries an ngspice failure signature (timestep too small,
trouble with node, singular matrix, gmin stepping failed). ngspice runs as
a shared library here, so there is no exit code - the log is the only
place this shows.

**Cheapest fix first:** the analog model the bench simulates, in `tb/`.
Check for a floating node, a missing DC path to ground, or an ideal source
driving a capacitor with no series resistance. A behavioral (`ideal`)
model converges far more easily than a transistor-level one; use it for
cosim unless the brief needs the real devices.

**Trap:** `docs/spikes/dcosim.md` records a transistor-level pair that did
not converge inside the bridge for reasons outside the netlist. Do not
spend the budget there - escalate with the log excerpt.
