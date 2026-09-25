# counter8

An 8-bit free-running binary counter. Smallest rung on the `/vde` corpus
ladder - it exists so every gate has something real to run on.

## Behaviour

- REQ-INC: `count` increments by 1 every rising edge of `clk` while `rst`
  is low.
- REQ-RST-SYNC: while `rst` is held high, `count` is 0 on every following
  rising edge of `clk` - synchronous reset, not asynchronous. `rst` has no
  effect on `count` between clock edges.
- REQ-WRAP: `count` wraps from 255 to 0 and keeps counting; there is no
  overflow flag.
- REQ-RST-PULSE: a reset asserted for exactly one clock while the counter
  is mid-run clears `count` to 0 on the very next edge, the same as a reset
  held at start-up, and counting resumes from 0 on the edge after it is
  released.

## Interface

| port  | dir | width | meaning |
|---|---|---|---|
| clk   | in  | 1 | free-running clock |
| rst   | in  | 1 | synchronous, active-high reset |
| count | out | 8 | the running count |

One clock domain, `clk`. No asynchronous inputs.

## Clock plan

- `clk` period 10 ns (100 MHz). The brief gives no frequency; 10 ns is the
  architect's choice - the smallest round period that keeps an 8-bit
  incrementer's single-cycle timing meaningful in any of the flow's PDKs,
  and the value the timing gate signs off against.
- No other clocks, no clock enables, no gated clocks.

## Architecture

Single module `counter8`: an 8-bit register with a synchronous, active-high
reset and a +1 incrementer feeding it; `count` is the register's output,
unregistered further. No internal module breakdown - there is nothing to
split.

Decisions taken by the architect where the brief is silent:

- Power-on value of `count` before the first reset is unspecified; the RTL
  may leave it undefined (no reset value on the register beyond the
  synchronous one), and the testbench must assert `rst` before checking
  `count`. No requirement is added for it.
- Nothing in the block is `must_keep`: every element drives `count`.

See `spec.yaml` for the machine-readable requirements this prose maps to.
