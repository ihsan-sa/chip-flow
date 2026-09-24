# /vde - AI digital design engineer

Usage:
- `/vde <task>` - anything a digital designer would be asked: "design an
  8-bit counter from corpus/vde/counter8/spec.md", "add a test for
  REQ-RESET", "prove the reset property", "what's the mutation kill rate",
  "harden this to GDS", "the timing gate is failing", "review this block",
  "is this ready to release" - and the full spec-to-release pipeline, which
  is one of those tasks like any other. Attach or name a spec file freely;
  it becomes the workspace's `brief/`.
- `/vde --resume <workspace>` - continue a run from its `state.json` (e.g.
  `/vde --resume blocks/counter8`).

On invocation:
1. Read `skills/vde/SKILL.md` (the orchestrator playbook) and follow it
   exactly - it defines the front door, the phase machine, gates, the fix
   loop, state recording, and human checkpoints.
2. Route the task FIRST:

       eda python engine/scripts/task_router.py --skill vde --task "<the user's words>" [--workspace blocks/<name>]

   exit 0 -> follow `recipe.doc` (when non-null) + `recipe.steps`; exit 1 ->
   the payload says whether to classify (`--verb`), ask the user, or clear
   a blocked precondition. Never hand-assemble a recipe the table already
   has.
3. A task that needs a NEW workspace (a full run, or importing an outside
   block) derives a short kebab-case block name, creates `blocks/<name>/`,
   and runs `state.py init` as the recipe's first step.

The orchestrator never opens design files; all design work happens in
spawned subagents using the role prompts in `skills/vde/agents/`.
