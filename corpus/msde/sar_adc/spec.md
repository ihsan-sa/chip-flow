# sar_adc

An 8-bit SAR (successive-approximation) ADC (docs/design.md section 3's
`/msde` corpus list, "### M10.": "the SAR ADC join[s] as specs with faults
and may stay red").

**This rung is a spec with faults only.** There is no reference design yet
- no digital RTL, no analog netlist, no nested `/vde`/`/ade` workspaces -
so only `split` runs here (docs/design.md 1.5 msde table). Its `ladder.md`
row may stay red until a later milestone gives it a reference design and
wires up `cosim`, `top_drc`, `top_lvs` and `release`.

## What the block does

The digital side is the SAR control logic: `clk` and `start` (TT pins) run
a successive-approximation search that drives `dac_code` one bit at a time,
reads back `cmp_out` after each step, and on completion raises `done` with
the final 8-bit `result`. The analog side is a sample-and-hold that closes
on `sample`, a capacitive (or R2R) DAC that converts `dac_code` to a trial
voltage, and a comparator whose output is `cmp_out`.

## Crossing signals

| signal    | direction | level     | domain   | width |
|-----------|-----------|-----------|----------|-------|
| dac_code  | d2a       | cmos_3v3  | clk_sys  | 8     |
| sample    | d2a       | cmos_3v3  | clk_sys  | 1     |
| cmp_out   | a2d       | cmos_3v3  | clk_sys  | 1     |
| comp_clk  | d2a       | cmos_3v3  | clk_sys  | 1     |

## Top-level measures (a future cosim bench)

Once a reference design exists, a `cosim` bench would check, at gf180
3.3 V supply:

- A DC input at 0 V, 1.65 V and 3.2 V each converts to within ±1 LSB
  (12.9 mV at 3.3 V full scale / 256 codes) of the expected 8-bit code.
- Conversion time: `start` to `done` completes within 8 `comp_clk` cycles
  (one bit decision per cycle) plus one sample-and-hold settling cycle.
- `done` stays low until the 8th bit decision latches, and `result` holds
  steady until the next `start`.

None of these run yet - there is no bench to run them in. They are
recorded here as the contract a reference design would have to meet.
