# requirement_no_test (sim)

A requirement with `check: sim` or `check: both` has no test anywhere
under `tb/` carrying its `# req: <ID>` tag on the line before the
`@cocotb.test()` decorator. Routes to `testbench`.

**Cheapest fix first:** check spelling and placement first - the tag must
be on the line IMMEDIATELY before the decorator (a blank line or a comment
between them breaks the scan), and the id must match `spec.yaml` exactly,
including case. Only write a genuinely new test once you've ruled out a
tagging mistake on an existing one.

**Trap:** `@cocotb.test(expect_fail=True)`/`expect_error=...` tests are
deliberately excluded from tag scanning (`cocotblib.EXPECT_FAILURE_RE`) -
a requirement covered ONLY by an expect-fail test still reads as
untested, correctly; write a normal test for it too.
