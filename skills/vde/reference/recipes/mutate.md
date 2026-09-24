# mutate - score the test, never the design

Runs `mutate` alone: yosys mutates the current RTL (fixed seed, N
mutants - inverted conditions, stuck outputs, removed resets, off-by-one
constants, swapped operators), and the visible suite plus bounded formal
run against each mutant. A mutant nothing catches "survived."

## Reading the result

Kill rate must clear 0.9, AND no survivor may land in a must-kill class
(`reset_removed`, `output_stuck`, `condition_inverted` -
`check_mutate.MUST_KILL_CLASSES`) regardless of the overall rate - a suite
that happens to kill 95% of mutants while missing every reset-removed one
has a specific, dangerous blind spot the aggregate number hides.

## This is the one gate whose failure is never the design's fault

A `mutate` failure means the TESTBENCH did not notice the design could be
wrong - full stop. `cluster_violations.FIXER_HINTS` already routes every
`survivor_*` kind and `kill_rate_below_threshold` to the `testbench`
domain; the fixer that picks up the work order edits `tb/`, never `rtl/`.
If you find yourself wanting to "just also improve the RTL a bit while
you're in there" - don't; that is scope creep into a different agent's
job and a different gate's evidence.

## Cost

The slowest single gate in the corpus (`docs/design.md` section 3) -
don't call this verb speculatively; let P4's own sequence run it once per
real RTL or testbench change.
