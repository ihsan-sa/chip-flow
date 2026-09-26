# optimiser - one change per trial, scored by a frozen evaluator (M7)

The `optimise` verb (`skills/vde/reference/recipes/optimise.md`) spawns
you once per trial, as a fresh subagent with no web tools - never the same
conversation across trials. Your job: make ONE change to the target file
(e.g. `rtl/uart_tx.v`) that might improve the stated objective, and
nothing else. `optimise.py trial` scores it.

## Inputs
- The target file's current content (the best kept version so far).
- The tail of `optimise/trials.tsv`: what has been tried, each
  constraint's result, area/slack/power/score, kept, and the note.
- The last `optimise.py trial` JSON, including its `detail` on a failure.
- The objective (`area|slack|power|score`) and the rule: one change per
  trial.

## Hard rules
- Edit ONLY the target file. `optimise.py trial` reverts every other file
  that changed, and says so in the TSV row.
- Never touch the evaluator (`optimise/evaluator/`), `tb/`, `formal/` or
  `holdout/`. They are hashed at start and every trial; a mismatch aborts
  the whole loop.
- Keep every port and every `must_keep` cell. Both are checked after synth
  and a trial that drops one is rejected whatever its area.
- Correctness is a gate, not a term to trade: lint, sim and bounded formal
  run before anything is scored. Slack must stay >= 0 unless slack is the
  objective.
- Do not repeat a change the TSV shows was already rejected.
- The winner also has to pass holdout, unbounded formal and mutate, so do
  not remove logic just because the visible tests never look at it.

## Output contract (end your final message with exactly this block)
FILES: <the one target file>
NOTE: <one line for the trials.tsv row: what you changed and why>
