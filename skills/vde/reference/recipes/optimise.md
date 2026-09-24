# optimise - placeholder until M7

`docs/design.md` section 4 describes the real loop: one file edited per
trial, a frozen evaluator (fixed synth script, locked clock period and
port list, `must_keep` checked after synth, the evaluator directory itself
hashed at start and re-checked every trial) scores the change, and it
stays only if the metric improved - otherwise `git checkout -- <target>`.
That is `optimise.py`, and it is `### M7.`'s build, not this milestone's.

## What this verb does today

Records that optimisation was requested (`state.py decision`) against the
block's current `synth` (and, once M4 lands, `timing`) baseline, and
stops. It never spawns the `optimiser` role (`skills/vde/agents/
optimiser.md` explains why the file exists anyway).

## Why not just hand-edit the RTL for a quick win

That is precisely the metric-gaming section 4 is written to prevent: area
measured by whatever script the agent happens to run (not the fixed
synth), a design that gets "faster" by moving the clock period, or
"smaller" by quietly dropping a `must_keep` instance from the outside.
None of that produces a number anyone should trust, and this verb would
rather report nothing than report a fake one.
