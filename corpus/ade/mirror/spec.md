# mirror - NMOS current mirror

The first rung of `/ade`'s corpus (docs/design.md section 3, "### M8."):
the simplest real analog block the sim gates have to validate end to end.

## What it does

A two-transistor NMOS current mirror. `xmref` is diode-connected (its gate
tied to its own drain) and sets the gate voltage the whole mirror shares;
`xmout` copies that gate voltage and, sized `Wout = 2 * Wref` (same `L`),
sinks roughly twice the reference current into `iout`.

## Interface

`current_mirror(iref_node, iout, vdd, vss)` - `iref_node` is the diode-
connected reference input (a bench drives a fixed reference current into
it), `iout` is the mirrored output (a bench measures the current a load
pulls through it), `vdd`/`vss` are the supply rails.

## Requirement

- `iout` tracks `10 uA * (Wout/Wref) = 20 uA` closely enough to be useful as
  a bias/reference current source, across process, temperature and supply
  (docs/design.md 5's default PVT corner set) - measured as the raw output
  current (`iout_raw`) rather than a computed ratio, since ngspice's own
  `.measure ... PARAM` expression syntax turned out unreliable for chaining
  two `.measure` results together on this box's ngspice build; measuring
  the absolute current directly, with bounds set from this design's own
  real simulated range, is exactly as sensitive to the faults this corpus
  rung plants and needs no such chaining.

Real simulated values on this box (typical/ss/ff/sf/fs, gf180mcuD): 24.04,
22.24, 26.82, 22.30, 26.64 uA - all comfortably inside `tb/mirror_tb.
bounds.json`'s [18, 32] uA window; the planted "W and L swapped" fault
measures 0.106 uA, nowhere close.
