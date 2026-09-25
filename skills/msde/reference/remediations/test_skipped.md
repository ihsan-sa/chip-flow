# test_skipped (cosim)

A cosim test was skipped, so it never ran. A skipped test is not a pass.

**Cheapest fix first:** remove the skip marker or the condition that
triggers it, in `tb/test_*.py`.

**Trap:** skipping the slow transistor-level case to get a green gate is
exactly what this finding exists to catch.
