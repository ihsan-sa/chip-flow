# sensor_counted

A ring oscillator into a counter (docs/design.md section 3's `/msde` corpus
list). The analog block free-runs a ring oscillator whose frequency the
digital side counts over a fixed gate window; a digital enable powers the
oscillator down between measurements.

This rung exercises the `split` gate (docs/design.md 1.5 msde table): the
splitter's `interface.yaml` names every crossing signal, and the digital and
analog sides each carry their own copy of that list in `digital_spec.yaml` /
`analog_spec.yaml` (design.md section 5: "two specs carrying the same
entries, and split checks they agree").

The two nested `/vde` and `/ade` workspaces this block would drive at P2
(docs/design.md 1.4) are not built at M10 - they land once M4 and M9 merge.
`digital_spec.yaml` / `analog_spec.yaml` here are the two sides' declared
interface only, standing in for a real nested block's own spec.yaml
`interface:` section until then.

## Crossing signals

| signal   | direction | level     | domain   | width |
|----------|-----------|-----------|----------|-------|
| osc_out  | a2d       | cmos_3v3  | clk_free | 1     |
| osc_en   | d2a       | cmos_3v3  | clk_sys  | 1     |
