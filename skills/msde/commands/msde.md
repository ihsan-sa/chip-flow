# /msde - AI mixed-signal design engineer

Usage:
- `/msde <task>` - anything a mixed-signal lead would be asked: "design the
  counted sensor from corpus/msde/sensor_counted/spec.md", "split this into
  digital and analog", "widen the control word to 6 bits", "integrate the
  analog block", "run the co-simulation", "review this block", "is this
  ready to release" - and the full spec-to-release pipeline. Attach or
  name a spec file freely; it becomes the workspace's `brief/`.
- `/msde --resume <workspace>` - continue a run from its three
  `state.json` files (e.g. `/msde --resume blocks/sensor_counted`).

On invocation:
1. Read `skills/msde/SKILL.md` (the orchestrator playbook) and follow it
   exactly - the front door, the nested workspaces, the phase order, the
   gates, the interface cascade and the fix loop.
2. Route the task FIRST:

       ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/bin/eda python \
         ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/engine/scripts/task_router.py --skill msde \
         --task "<the user's words>" [--workspace blocks/<name>]

   exit 0 -> follow `recipe.doc` + `recipe.steps`; exit 1 -> classify
   (`--verb`), ask the user, clear a blocked precondition, or route a
   one-side task to that side's skill and nested workspace.
3. A task that needs a NEW workspace derives a short snake_case block
   name, creates `blocks/<name>/`, and runs `state.py init` as the
   recipe's first step.

The orchestrator never opens design files; msde work happens in spawned
subagents using the role prompts in `skills/msde/agents/`, and each side's
work under its own skill's playbook.
