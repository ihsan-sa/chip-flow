# hold_violation (timing)

OpenSTA found negative worst hold slack in at least one corner - a race
independent of clock speed (hold cannot be fixed by relaxing the clock
period the way `setup_violation` sometimes can). Routes to `harden`.

**Cheapest fix first:** almost always a harden/config.json clock-tree or
buffering setting (hold fixing is normally an automated LibreLane step);
re-run harden with hold-fixing enabled before suspecting the RTL.

**Trap:** never treat this as fixable by loosening the clock period - hold
violations do not improve with a slower clock, and reporting that as the
fix hides a real clock-tree problem.
