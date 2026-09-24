# toggle_coverage_below_threshold (cover)

Verilator toggle coverage on the DUT is below 90% - some bit of some
signal never changed value across the whole visible suite. Routes to
`testbench`.

**Cheapest fix first:** a signal that never toggles a particular bit is
usually one whose full input range the suite never exercises (a counter
that never wraps in a short test, a status field whose error bit is never
set) - add a test that drives the specific condition needed to flip that
bit.

**Trap:** a signal's high bit(s) failing to toggle often means the
visible suite never drives the design near its extreme values (max count,
max payload size) - check the value range you are actually testing, not
just the number of test cases.
