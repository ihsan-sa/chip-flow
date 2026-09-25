# measure_out_of_bounds (cosim)

A top-level measure is outside its bound - the finding this gate exists
for. The corpus faults behind it: a divider with the wrong ratio, a
control word with inverted polarity.

**Cheapest fix first:** compute what each side should produce from its
own spec, then find which side does not. A divided frequency off by a
clean factor is usually the digital divider; a code-to-voltage curve that
runs backwards is usually a polarity error on one side of the interface.
The defect is almost always inside a side: report it for that side's own
router, which re-runs that side's gates, then re-run `cosim` here.

**Trap:** never move the bound toward the measured value. If the bound
itself contradicts the brief, that is the only case the bench's bounds
file changes, and it is recorded as a decision.
