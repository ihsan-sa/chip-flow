# survivor_connection_removed (bench_strength)

One terminal of a device was disconnected and no measure noticed. For a
MOSFET that is the bulk, or the drain when the bulk is tied to the device's
own source (floating that bulk would change nothing); the survivor's message
names which one. Routes to `testbench`.

**Cheapest fix first:** read the survivor's `deltas` in the gate's facts.
If a spec measure moved but stayed inside the spec, give its bound a
`sensitivity` in `tb/*.bounds.json`, max(3 sigma, 2%) of its mc spread
(2% when no sigma is known). If nothing moved, a measure that only reads
a DC point may not see a floating bulk: fix the bench's `.measure` for a
spec measure the terminal actually drives, such as an output resistance.
A bench may not score a measure the spec doesn't declare, so a missing
one is a spec change: say so under OPEN.

**Trap:** some disconnections really are invisible at every port. If you
can show that, say so and escalate rather than bend a bound around it (the
same honesty rule as vde's equivalent mutants).
