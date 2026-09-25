# sensor_counted / digital

The digital side of the `/msde` `sensor_counted` block (corpus/msde/sensor_counted/spec.md):
counts rising edges of an analog ring oscillator's free-running square wave
over a fixed gate window, and powers the oscillator down between
measurements. This is the nested `/vde` workspace source for that digital
side (docs/design.md 1.4's "the two nested `/vde` and `/ade` workspaces this
block would drive at P2"), laid out exactly like a standalone `/vde` rung
(corpus/vde/counter8) so the same engine runs it.

## Behaviour

- `osc_en` is `en`, wired straight through combinationally (registering it
  would delay the oscillator's own power-up/down by a clock relative to
  `en`, which the analog side does not need and nothing in this block asks
  for).
- `osc_out` is an ASYNCHRONOUS square wave from the analog ring oscillator
  (roughly 1-12 MHz) - it is not related to `clk`'s edges at all. It passes
  through a 2-flop synchroniser into the `clk` domain before anything else
  touches it.
- A rising-edge detector on the synchronised signal counts edges. The count
  accumulates over a fixed gate window of 1024 `clk` cycles.
- At the end of each window the accumulated edge count is latched into
  `count`, saturating at 255 (it never wraps), and `valid` goes high. Once
  asserted, `valid` stays high across every following window until reset -
  it is a "there is a real reading in `count`" latch, not a per-window
  strobe.
- Counting (both the window cycle counter and the edge counter) only
  happens while `en` is high. While `en` is low the window counter is held
  at its reset value of 0 - not frozen part-way through a window, so
  re-enabling always starts a fresh, full window.
- `rst_n` is a SYNCHRONOUS, active-low reset: while `rst_n` is low, on every
  following clock edge `count` is 0, `valid` is 0 and both internal
  counters are held at 0.

## Interface

| port     | dir | width | meaning |
|----------|-----|-------|---------|
| clk      | in  | 1 | free-running system clock, 50 MHz (20 ns period) |
| rst_n    | in  | 1 | synchronous, active-low reset |
| en       | in  | 1 | measurement enable; also directly drives `osc_en` |
| osc_out  | in  | 1 | async square wave from the analog ring oscillator |
| osc_en   | out | 1 | oscillator power-down enable (= `en`) |
| count    | out | 8 | edges counted in the last completed gate window, saturating at 255 |
| valid    | out | 1 | a completed window's `count` is available; latched until reset |

`osc_out`/`osc_en` are the same two crossing signals corpus/msde/sensor_counted's
own `interface.yaml` names.

See `spec.yaml` for the machine-readable requirements this spec.md prose
maps to.
