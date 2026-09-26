# optimise - the RTL loop against a frozen evaluator

docs/design.md section 4 and "### M7.". `optimise.py start --target
rtl/<file>.v` freezes the evaluator into `optimise/evaluator/` and hashes
it: the synth script, the liberty file, an SDC built from the spec's clock,
`tb/`, `formal/`, the holdout's hash, the port list and `must_keep`. It then
scores the starting RTL as trial 0. Each trial, a fresh `optimiser` subagent
makes one change to the target file and `optimise.py trial --note "..."`
scores it. `optimise.py finish` runs the full gates on the winner.

## Before you start

- The starting RTL must already pass its gates. `start` refuses RTL that
  fails lint, sim or bounded formal, because the loop makes working code
  good and never makes broken code pass.
- The workspace must be a git repo with a clean tree. A kept trial is a git
  commit of the target, and a rejected one is `git checkout -- <target>`.
- `spec.yaml` needs `ports` (the loop locks them) and should list every
  instance the tests cannot see but the design needs under `must_keep`.
- Pick the objective: `area` (the default), `slack`, `power`, or `score`,
  which is area and power relative to trial 0. Unless the objective is
  `slack`, slack >= 0 at the spec's clock is a constraint.

## One trial

The optimiser gets the target file, the tail of `optimise/trials.tsv`, the
last trial's JSON and the objective, and changes one thing. `trial` then:

1. reverts every changed file other than the target;
2. re-hashes the evaluator and aborts the whole loop on a mismatch;
3. runs lint, sim and formal (depth capped) in a scratch workspace built
   from the frozen copies;
4. if they pass, runs yosys for area, checks the ports and every
   `must_keep` cell survived, and runs OpenSTA for slack and for power from
   the testbench's VCD;
5. keeps the change only if every constraint passed and the score beat the
   best so far, and writes one TSV row either way.

Stop when `trial` reports a non-null `stop`: N trials, the wall budget, or
K non-improving trials in a row.

## After the winner

`finish` runs holdout, formal at the spec's own depth and mutate on the
winner. A winner that fails any of them is discarded and the last kept
trial that passes (or the starting RTL) is restored, and the failures are
in the payload's `tried`. That is what stops a change that deletes logic
the visible tests never exercised. The winner is new RTL, so the verb
records an `rtl_edit` and every gate from lint to release reruns.

## What this verb will not do

Hand-edit RTL outside the loop for a quick win, move the clock period, drop
a port, or edit the tests to make a trial pass. Each of those changes the
evaluator, and a number from a changed evaluator is not one anyone should
trust.
