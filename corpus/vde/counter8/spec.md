# counter8

An 8-bit free-running binary counter.

## Behaviour

- `count` increments by 1 every rising edge of `clk`.
- While `rst` is held high, `count` is 0 on every following clock edge -
  synchronous reset, not asynchronous.
- `count` wraps from 255 to 0 and keeps counting; there is no overflow flag.
- A reset asserted for exactly one clock while the counter is mid-run must
  clear `count` to 0 on the very next edge, the same as a reset held at
  start-up.
- The target clock period is 20 ns (50 MHz).

## Interface

| port  | dir | width | meaning |
|---|---|---|---|
| clk   | in  | 1 | free-running clock |
| rst   | in  | 1 | synchronous, active-high reset |
| count | out | 8 | the running count |
