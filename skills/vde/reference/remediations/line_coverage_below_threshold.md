# line_coverage_below_threshold (cover)

Verilator line coverage on the DUT's own `rtl/` files, under the same
tests `sim` runs, is below 95%. Routes to `testbench` - "coverage is what
the tests never reached" (`docs/design.md` section 2), so the fix is
almost always a test that exercises the uncovered lines, not an RTL edit.

**False positive class:** truly unreachable code (a defensive default arm
that provably cannot execute given the design's own constraints). This
needs a `spec.yaml` exclusion WITH a reason, not a silently ignored
finding - `check_cover.py` requires the reason to exist; an exclusion
with no reason is itself refused.

**Cheapest fix first:** the finding lists exact uncovered `file:line`
pairs (also visible as individual `line_not_covered` info findings) - add
a test that drives the design down that specific path rather than padding
the suite generally.

**Trap:** an unreachable-looking line is often reachable through a
sequence of inputs the visible tests never tried (a specific state built
up over several cycles, not a single-cycle stimulus) - check reachability
carefully before writing an exclusion.
