# test_failed (cosim)

A cocotb test in the cosim bench failed its own assertion. It is reported
alongside any measure finding, never instead of one.

**Cheapest fix first:** read the other findings first - an assertion on a
measure that is also `measure_out_of_bounds` is the same defect. With no
other finding, read the assertion's message: a wait that timed out means
an edge never came (see `digital_side_never_toggled`).

**Trap:** the engine routes this kind to `rtl` (its vde meaning). In msde
the defect may be in the bench, either side, or the join - decide whose
it is before anyone edits anything.
