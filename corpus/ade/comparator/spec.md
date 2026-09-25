# comparator - clocked StrongARM latch comparator

The second rung of `/ade`'s corpus (docs/design.md, "### M9."): a clocked
comparator with no static current, taken from spec to a released layout.

## What it does

A dynamic comparator on the 3.3 V devices of gf180mcuD. While `clk` is low
it resets: both outputs are pulled to `vdd` and it draws no static current.
When `clk` rises it compares `inp` with `inn` and regenerates the difference
to full logic levels: `outp` goes high and `outn` low when `inp > inn`, and
the other way round when `inn > inp`. The decision holds until `clk` falls.

## Interface

`strongarm_comparator(inp, inn, clk, outp, outn, vdd, vss)`, in that pin
order. `inp`/`inn` are the differential inputs, `clk` the clock, `outp`/
`outn` the complementary outputs, `vdd`/`vss` the rails. Supply 3.3 V.

Use the StrongARM latch topology with these eleven devices, named exactly:
a clocked NMOS tail `xmtail`; the NMOS input pair `xmip` (gate `inp`) and
`xmin` (gate `inn`); the cross-coupled latch NMOS `xmln1`, `xmln2` and PMOS
`xmlp1`, `xmlp2`; and four clocked PMOS reset switches `xmrp`, `xmrn` (the
input pair's drains) and `xmrop`, `xmron` (the outputs). Every device is a
`nfet_03v3` or `pfet_03v3`.

## Requirement

The bench: `clk` a 0-to-`vdd` pulse, 10 ns period, rising at 5 ns and 15 ns
with 50 ps edges and about 5 ns high. The inputs sit at mid-supply
(1.65 V) with `inp` 1 mV above `inn` for the first decision; they swap
during the reset at 11 ns, so `inn` is 1 mV above `inp` for the second. Each
output carries a 10 fF load to ground. At every corner of docs/design.md
section 5's default PVT set:

- `vdiff_pos`: `v(outp) - v(outn)` at 9.5 ns (late in the first decision)
  is at least 2.5 V.
- `vdiff_neg`: the same difference at 19.5 ns (the second decision) is at
  most -2.5 V.
- `tdelay`: from `clk` rising through 1.4 V to `outn` falling through
  1.4 V, first decision, is between 0.1 ns and 0.6 ns.
- `vreset`: `v(outn)` at 14.5 ns, in the reset between the decisions, is at
  least 2.6 V.

After layout, at the typical corner, the extracted netlist with its wire
capacitance and resistance runs the same bench: both decisions still at
least 2.5 V apart with the right sign, the reset level at least 2.6 V, and
`tdelay` between 0.1 ns and 1.0 ns.

Real simulated `tdelay` on this box (typical/ss/ff/sf/fs): 0.357, 0.494,
0.268, 0.474, 0.288 ns; the outputs reach the rails at every corner.
