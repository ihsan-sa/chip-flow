# precheck_failed (precheck)

Tiny Tapeout's own precheck.py flagged a testcase against the hardened
GDS/netlist (wrong top module name, a tt_pins mismatch, a submission
metadata problem). Routes to `harden`.

**Cheapest fix first:** the finding names the failing testcase and
precheck's own error message directly - most cases are a mismatch between
spec.yaml's `tt_pins`/top name and what harden/config.json or the RTL
module actually names, and are fixed by making the two agree.

**Trap:** this runs against the ALREADY-hardened design - fixing the
mismatch (RTL module name, tt_pins map, or harden/config.json) requires a
harden re-run before precheck can be re-checked; it will not pick up an
RTL-only edit on its own.
