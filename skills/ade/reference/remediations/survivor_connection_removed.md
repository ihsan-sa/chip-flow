# survivor_connection_removed (bench_strength)

A device's last terminal (usually the bulk) was disconnected and no
measure noticed. Routes to `testbench`.

**Cheapest fix first:** a measure that only reads a DC point may not see a
floating bulk. Add a measure the terminal actually drives, such as an
output resistance, or a current at a second output voltage.

**Trap:** some disconnections really are invisible at every port. If you
can show that, say so and escalate rather than bend a bound around it (the
same honesty rule as vde's equivalent mutants).

One such mutant is already accepted: the comparator's `xmtail` with its
bulk removed, because that bulk is tied to its source at vss. The gate
lists it under `accepted_equivalents` and fails every other survivor. Do
not add to that list yourself; a new equivalent is the owner's call.
