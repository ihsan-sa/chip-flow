# uart

A UART transmitter: one start bit, 8 data bits (LSB first), one even-parity
bit, one stop bit, each held `CLKS_PER_BIT` clocks. Second rung on the
`/vde` corpus ladder - harder than counter8, and the one the `holdout`
fault ("UART parity inverted where the visible tests do not look") is
written about.

## Behaviour

- Idle: `tx` is 1, `busy` is 0. (REQ-IDLE)
- A `start` pulse (one clock, while idle) with `data` present latches
  `data` and begins a frame: start bit (0), then `data[0]` through
  `data[7]`, then the parity bit (XOR of all 8 data bits - even parity),
  then the stop bit (1). Each bit is held for `CLKS_PER_BIT` clocks.
  (REQ-START-BIT, REQ-DATA-LSB-FIRST, REQ-PARITY-EVEN, REQ-STOP-BIT,
  REQ-BIT-HOLD, REQ-LATCH-ON-START)
- `busy` is 1 from the start pulse until the stop bit's hold completes, then
  returns to 0. (REQ-BUSY-FRAME)
- A `start` pulse while `busy` is 1 is ignored: the frame in flight is not
  disturbed and no second frame is queued. (REQ-START-WHILE-BUSY - implied
  by "one-clock pulse, while idle")
- Synchronous, active-high `rst` returns the transmitter to idle: on the
  clock after `rst` is sampled high, `tx` is 1 and `busy` is 0.
  (REQ-RESET)

## Frame timing

A frame is 11 bits, so `busy` is high for exactly `11 * CLKS_PER_BIT`
clocks after the start pulse. Bit `k` of the frame (0 = start, 1..8 =
`data[k-1]`, 9 = parity, 10 = stop) is on `tx` for clocks
`k * CLKS_PER_BIT` to `(k + 1) * CLKS_PER_BIT - 1` counted from the first
clock of the frame. The stop bit and the idle line are both 1, so `tx` is
continuously 1 from the start of the stop bit until the next frame's start
bit.

## Interface

| port  | dir | width | meaning |
|---|---|---|---|
| clk   | in  | 1 | free-running clock |
| rst   | in  | 1 | synchronous, active-high reset |
| start | in  | 1 | one-clock pulse: begin a frame |
| data  | in  | 8 | the byte to send, latched on `start` |
| tx    | out | 1 | the serial line |
| busy  | out | 1 | high for the duration of a frame |

`CLKS_PER_BIT` is a module parameter (clocks per bit), default **4**
(`params.CLKS_PER_BIT` in `spec.yaml`). The brief does not fix its value;
4 keeps a frame at 44 clocks so simulation and formal stay fast, while the
per-bit hold counter is still a real 2-bit counter, so an off-by-one in the
hold is visible where a default of 1 or 2 would hide it. The testbench and
the formal harness use the same default; the RTL must work for any
`CLKS_PER_BIT >= 1`.

Clock: `clk` only, one domain, 10 ns period (100 MHz). The brief states no
frequency; 10 ns is the smallest round period at which every timing
requirement is expressed in whole clocks and the GF180 tile closes without
effort. Nothing here depends on the absolute period.

## Architecture

A single module, `uart`, is enough - no block list. Inside it the
rtl-writer will need a latched data byte, a bit index (0..10), a per-bit
hold counter (0..`CLKS_PER_BIT-1`) and a busy flag; parity is the XOR of
the latched byte, computed when the parity bit is driven or at latch time,
either is fine. None of that is an interface, so none of it is fixed here.
There is no `must_keep`: every internal state is observable from `tx` and
`busy`.

Timing conventions the rtl-writer and tb-writer must both follow (these
resolve what the brief leaves open; they do not change any requirement):

- `tx` and `busy` are registered outputs. A `start` sampled high on clock
  edge N (while idle and `rst` low) latches `data` on that edge; from edge
  N+1, `busy` is 1 and `tx` drives the start bit. Edge N+1 is "the first
  clock of the frame" in the frame-timing section above.
- `busy` is 1 for exactly `11 * CLKS_PER_BIT` clocks (edges N+1 through
  N+44 with the default), covering the full stop-bit hold, and is 0 again
  from the clock after the stop bit's hold completes. `tx` is 1 on that
  clock and stays 1 while idle, so the stop bit runs seamlessly into idle.
- A `start` sampled on the same edge as `rst` is lost: reset wins, the
  transmitter is idle on the next clock and no frame begins. `rst` mid-frame
  abandons the frame in the same way (REQ-RESET).
- A `start` sampled while `busy` is 1 is ignored outright - not queued, not
  latched (REQ-START-WHILE-BUSY). A `start` on the very clock `busy`
  returns to 0 (edge N+45 with the default) is accepted as a new frame,
  because `busy` is 0 on that edge.
- `data` is sampled only on the accepting `start` edge; its value at any
  other time has no effect (REQ-LATCH-ON-START).

See `spec.yaml` for the machine-readable requirements this spec.md prose
maps to.
