# test_failed (sim)

A visible cocotb test ran and its assertion failed. Since the tests are
written first, from the spec alone, before any RTL exists (`docs/
design.md` section 2), this almost always means the DESIGN does not do
what the test - and therefore the spec - says it should. Routes to `rtl`.

**False positive class:** a genuine test bug (an off-by-one in the test's
own reference model, a race between drive and sample not anchored to the
same clock edge - see `corpus/vde/counter8/tb/test_counter8.py`'s own
`FallingEdge`-only convention). Real, but less common than a real design
bug - read the assertion's expected-vs-actual message before assuming the
test is wrong.

**Cheapest fix first:** reproduce the exact failing case by hand against
the spec text - does the RTL's actual behavior match what `spec.md`/
`spec.yaml`'s requirement says, or does the test's expectation? Fix
whichever one is actually wrong; if it's the test, this finding is not
yours to close (report it in OPEN, the orchestrator re-routes to
tb-writer, never silently edit `tb/` from the rtl domain).

**Trap:** a failure right after a reset edge is almost always a one-cycle
timing assumption mismatch (registered vs. combinational reset release) -
check exactly which edge the RTL's reset takes effect on against exactly
which edge the test samples.
