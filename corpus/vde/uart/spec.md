# uart

A UART transmitter: one start bit, 8 data bits (LSB first), one even-parity
bit, one stop bit, each held `CLKS_PER_BIT` clocks. Second rung on the
`/vde` corpus ladder (docs/design.md section 3) - harder than counter8, and
the one gates.yaml's own `holdout` fault ("UART parity inverted where the
visible tests do not look") is written about.

## Behaviour

- Idle: `tx` is 1, `busy` is 0.
- A `start` pulse (one clock, while idle) with `data` present latches
  `data` and begins a frame: start bit (0), then `data[0]` through
  `data[7]`, then the parity bit (XOR of all 8 data bits - even parity),
  then the stop bit (1). Each bit is held for `CLKS_PER_BIT` clocks.
- `busy` is 1 from the start pulse until the stop bit's hold completes, then
  returns to 0.
- Synchronous, active-high `rst` returns the transmitter to idle.

## Interface

| port  | dir | width | meaning |
|---|---|---|---|
| clk   | in  | 1 | free-running clock |
| rst   | in  | 1 | synchronous, active-high reset |
| start | in  | 1 | one-clock pulse: begin a frame |
| data  | in  | 8 | the byte to send, latched on `start` |
| tx    | out | 1 | the serial line |
| busy  | out | 1 | high for the duration of a frame |

See `spec.yaml` for the machine-readable requirements this spec.md prose
maps to.
