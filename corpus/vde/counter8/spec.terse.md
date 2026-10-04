# counter8

An 8-bit free-running binary counter, target clock period 20 ns (50 MHz).

| port  | dir | width | meaning |
|---|---|---|---|
| clk   | in  | 1 | free-running clock |
| rst   | in  | 1 | synchronous, active-high reset |
| count | out | 8 | the running count |

- `count` increments by 1 every rising edge of `clk`.
- While `rst` is high, `count` is 0 on every following clock edge (synchronous, not asynchronous).
- `count` wraps from 255 to 0 and keeps counting; no overflow flag.
- A reset asserted for exactly one clock mid-run clears `count` to 0 on the very next edge, the same as a reset held at start-up.
