# untagged_holdout_test (holdout)

A test under `holdout/` has no `# req: ID` tag on the line before its
`@cocotb.test()` decorator - its result cannot be reported by requirement
id alone, which is the only thing a holdout failure is allowed to reveal.
Routes to `testbench` (the tb-writer owns `holdout/` too).

**Cheapest fix first:** add the tag, matching a real `spec.yaml`
requirement id exactly. Every held-out test must trace to a requirement -
one that does not is testing something the spec never asked for.
