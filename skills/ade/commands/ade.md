# /ade - AI analog design engineer

Usage:
- `/ade <task>` - anything an analog designer would be asked: "design a
  current mirror from corpus/ade/mirror/spec.md", "resize xmout", "add the
  ss corner", "run the sizing optimiser", "write the layout", "the drc
  gate is failing", "review this block", "is this ready to release" - and
  the full spec-to-release pipeline, which is one of those tasks like any
  other. Attach or name a spec file freely; it becomes the workspace's
  `brief/`.
- `/ade --resume <workspace>` - continue a run from its `state.json` (e.g.
  `/ade --resume blocks/mirror`).

On invocation:
1. Read `skills/ade/SKILL.md` (the orchestrator playbook) and follow it
   exactly - it defines the front door, the phase machine, gates, the fix
   loop, state recording, and human checkpoints.
2. Route the task FIRST:

       ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/bin/eda python \
         ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/engine/scripts/task_router.py --skill ade \
         --task "<the user's words>" [--workspace blocks/<name>]

   exit 0 -> follow `recipe.doc` (when non-null) + `recipe.steps`; exit 1 ->
   the payload says whether to classify (`--verb`), ask the user, or clear
   a blocked precondition. Never hand-assemble a recipe the table already
   has.
3. A task that needs a NEW workspace (a full run, or importing an outside
   block) derives a short snake_case block name, creates `blocks/<name>/`,
   and runs `state.py init` as the recipe's first step. Copy the user's
   spec file(s) into `brief/` verbatim - never a corpus rung's own
   `spec.yaml`, `netlist/`, `tb/`, `sizing/` or `layout/`, which are its
   answer key.

The orchestrator never opens design files (`netlist/*.cir`, `tb/*.cir`,
`layout/gen_*.py`); all design work happens in spawned subagents using the
role prompts in `skills/ade/agents/`.
