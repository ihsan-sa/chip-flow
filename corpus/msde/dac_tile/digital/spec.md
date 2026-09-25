# dac_tile / digital

The digital side of the `/msde` `dac_tile` block (corpus/msde/dac_tile/spec.md):
a 2-bit register holding the DAC code the analog cell converts. This is the
nested `/vde` workspace source for that side, laid out like a standalone
`/vde` rung so the same engine runs it.

## Behaviour

- `code` is a 2-bit register loaded from `code_in` on every rising edge of
  `clk`.
- `bmsb` is `code[1]` and `blsb` is `code[0]`, straight from the register.
- `rst_n` is a SYNCHRONOUS, active-low reset: while it is low, every clock
  edge clears `code` to 00.

## Interface

| port    | dir | width | meaning |
|---------|-----|-------|---------|
| clk     | in  | 1 | system clock, 50 MHz (20 ns period) |
| rst_n   | in  | 1 | synchronous, active-low reset |
| code_in | in  | 2 | next DAC code, `ui_in[1:0]` on the tile |
| bmsb    | out | 1 | DAC code MSB, to the analog cell |
| blsb    | out | 1 | DAC code LSB, to the analog cell |

`bmsb`/`blsb` are the two crossing signals corpus/msde/dac_tile's own
`interface.yaml` names.

See `spec.yaml` for the machine-readable requirements this prose maps to.
