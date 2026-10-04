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

## Architecture sketch

One 8-bit register and an incrementer, nothing else. On each rising edge of
`clk`, if `rst` is high the register loads 0, otherwise it loads its own
value plus 1. `count` is the register output. The 8-bit add drops its carry,
which is what makes the wrap from 255 to 0 happen with no extra logic. There
is no state beyond the register, so no reset-recovery or start-up sequencing
is needed. Reset goes in the clocked block only: it must not appear in the
sensitivity list, and it must not be registered separately before it is used.

## Timing detail

Reset value is 0. `rst` is sampled at each rising edge of `clk`, and the
result shows on `count` just after that same edge. Cycle by cycle, with `rst`
a one-clock pulse in the middle of a run (the value of `count` is what it
reads after the edge):

| edge | `rst` before edge | `count` after edge |
|---|---|---|
| 1 | 1 | 0 |
| 2 | 0 | 1 |
| 3 | 0 | 2 |
| 4 | 1 | 0 |
| 5 | 0 | 1 |
| 6 | 0 | 2 |

Edge 4 is the one-clock reset: `count` returns to 0 on that edge itself, not
one edge later, and counting resumes from 1 on the next edge. At the wrap:

| edge | `rst` before edge | `count` after edge |
|---|---|---|
| n | 0 | 254 |
| n+1 | 0 | 255 |
| n+2 | 0 | 0 |
| n+3 | 0 | 1 |

A reset that is still high keeps `count` at 0 on every following edge. When
`rst` and the wrap coincide, reset wins and `count` is 0.

## Test hints

A good testbench covers these cases:

- Reset at start-up held for several clocks, then released: `count` is 0 while
  held and counts 1, 2, 3 after release.
- Run a full 256 clocks and check the wrap 255 to 0, then a few more counts.
- A reset pulse of exactly one clock in the middle of a run, checked on the
  very next edge, at more than one count value.
- A reset held for many clocks mid-run, then released.
- Reset asserted on the clock where `count` is 255, so reset and wrap meet.
- Drive inputs away from the clock edge (for example just after the falling
  edge) so the test does not race the design, and compare against a small
  model of the expected count on every clock rather than a few spot values.
