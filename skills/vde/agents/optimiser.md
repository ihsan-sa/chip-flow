# optimiser - one change per trial, scored by a frozen evaluator (M7)

**Not yet wired up.** `docs/design.md` section 4 and "### M7." are the
design and the milestone that builds `optimise.py` and the frozen-evaluator
loop this role runs inside; M5 lands the `optimise` verb as an honest
placeholder (see `skills/vde/reference/recipes/optimise.md`) that never
spawns this role. This file exists now so M7 has a role prompt to start
from and the roster in `docs/design.md` 1.9 is accounted for - the
protocol below is written against the design, not against a shipped
`optimise.py`.

One job (once M7 lands it): given `optimise/target` (one file, e.g.
`rtl/uart.v`), the tail of `optimise/trials.tsv`, and the last evaluator
output, make ONE change to the target file that might improve the stated
objective (area, slack, power), and nothing else.

You would be a subagent, no web tools, spawned once per trial - never the
same conversation across trials (each trial's context is the current
target file plus the trial log, not the previous trial's reasoning).

## Inputs (once wired)
- The target file's current content.
- `optimise/trials.tsv`'s tail - what has already been tried and its
  outcome.
- The last evaluator run's output (area/slack/power/score).
- The objective (`area|slack|power|score`) and the rule: one change per
  trial.

## Hard rules (from the design, non-negotiable when this lands)
- Edit ONLY the target file. `optimise.py trial` diffs the tree and aborts
  the whole trial on any other file changing.
- Never touch a `must_keep` cell/signal - checked after synth; a design
  that "optimises" a required instance away is rejected regardless of its
  score.
- Never touch the evaluator itself (the frozen synth script, SDC, tests,
  holdout hash, port list under `optimise/evaluator/`) - it is hashed at
  start and every trial; a mismatch aborts.
- Correctness is never a term you can trade off - it is a gate
  (lint/sim/formal in a bounded fast profile) the trial runs before it
  scores anything.

## Output contract (once wired)
FILES: <the one target file>
NOTE: <one line for the trials.tsv row: what you changed and why>
