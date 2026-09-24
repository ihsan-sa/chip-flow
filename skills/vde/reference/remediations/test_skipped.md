# test_skipped (sim, holdout)

A cocotb test never ran to a pass/fail verdict - cocotb's own skip
mechanism fired, or an exception during setup aborted it before any
assertion. Routes to `testbench`: this is a test-infrastructure problem,
not a design one, regardless of which gate reported it.

**Cheapest fix first:** run the test module directly and read the full
traceback (`log/sim_build`'s console output, or `log/`'s cocotb results)
rather than guessing from the one-line finding - a skip is almost always
an exception (an attribute that does not exist on `dut`, a wrong signal
name) rather than a deliberate `pytest.mark.skip`-style decision, and this
flow has no legitimate reason to ship a skipped test.

**Trap:** a signal-name typo against the RTL's actual port names is the
most common cause and produces an `AttributeError` that reads, at a
glance, like an environment problem.
