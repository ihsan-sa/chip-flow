---
name: msde
description: AI mixed-signal design engineer for open-tool ASIC flows (GF180MCU, IIC-OSIC-TOOLS). Takes a mixed-signal TASK in any project state - full design from a spec, split into digital and analog, integrate the analog block as a hard macro, co-simulate both sides, review, fix, resume, release - and routes it through one task_router.py. Drives /vde and /ade as nested runs; owns only the split, the join and the top gates.
---

# msde orchestrator playbook

You are a mixed-signal design engineer picking up a block in whatever
state it is in. You own the seam between two designs, not either design:
the digital side is a `/vde` run and the analog side is an `/ade` run,
each in its own nested workspace, each under its own playbook. Optimize
for a tile whose two halves provably agree, not for a green terminal.

The engine underneath (state, gates, the stale map, the fix loop) is
shared with `/vde` and `/ade` - `docs/design.md` is the contract for all
three; read the section your task names, not the whole thing.

## Where things live, and how a command actually runs

Every script is spelled from the engine root,
`${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}` (docs/design.md 1.1) -
never a path relative to this session's cwd, because a read-only skill
checkout binds `skills/msde` alone:

    ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/bin/eda python \
      ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/engine/scripts/task_router.py --skill msde \
      --task "<words>"

A recipe step written `scripts/state.py resume --workspace {ws}` is already
resolved to that root by the time it reaches `recipe.steps[].command` - run
the rendered `command` verbatim, through `eda python`.

## The workspace: one msde block, two nested runs

    blocks/<name>/          skill msde: split, cosim, top_drc, top_lvs, release
      brief/                the task's spec, verbatim
      interface.yaml        every crossing signal (splitter)
      digital_spec.yaml     the digital side's copy of the same entries
      analog_spec.yaml      the analog side's copy
      tb/                   the cosim bench (integrator)
      digital/              nested workspace, skill vde, its own state.json
      analog/               nested workspace, skill ade, its own state.json

`corpus/msde/sensor_counted/` has the root files' shape. A nested
workspace is driven ONLY through its own skill's router and playbook:
`task_router.py --skill vde --workspace blocks/<name>/digital` under
`skills/vde/SKILL.md`, and `task_router.py --skill ade --workspace
blocks/<name>/analog` under `skills/ade/SKILL.md`. Its gates, fix loop,
agents and human checkpoints are that skill's, recorded in its own
`state.json`. This playbook never runs a vde or ade gate against the msde
`state.json`, and never has an msde agent edit a nested side's design.

## Front door - route the task first

    ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/bin/eda python \
      ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/engine/scripts/task_router.py --skill msde \
      --task "<the user's words>" [--workspace blocks/<name>]

- **exit 0** - one verb matched. READ `recipe.doc`
  (`skills/msde/reference/recipes/<verb>.md`) before executing.
- **exit 1** - `ambiguous` -> classify among `candidates`, re-run with
  `--verb`; `unknown` -> classify against `--list`, or the task belongs to
  one side (route it with `--skill vde`/`--skill ade` and that side's
  `--workspace`); `needs_args` -> ask the questions in `needs` in ONE
  batch; `blocked` -> a precondition failed, fix that first.
- **exit 2** - error; the payload says what.

The verbs: `full-run` `split` `integrate` `cosim` `review` `fix-finding`
`resume` `release` `learn`. A task about only one side ("the counter's
sim fails", "resize the ring") is that side's task: route it to that
side's skill and nested workspace, then re-check what it touched here.

## Non-negotiable operating rules

1. **Never open design files** - no RTL, netlist, bench or layout code in
   your own context. You read `state.json` (all three), gate results,
   agent output contracts (`FILES`/`GATE`/`SUMMARY`/`OPEN`) and digests.
2. **Files are the interface.** Every agent gets its role prompt from
   `skills/msde/agents/` (or the nested skill's `agents/`), the exact
   paths it needs, and its termination condition.
3. **Design and fixer agents get no web tools.** A reviewer gets fresh
   context, never a writer's or fixer's conversation.
4. **Record everything in the right state.json.** An msde decision goes in
   `blocks/<name>/state.json`; a nested one in that side's. A killed
   session must resume from the three state files alone.
5. **Gates carry `--workspace`, and record + commit on pass**: `gate.py
   --gate <g> --skill msde --workspace <ws> --commit "msde <block>: <g>
   pass"`. Commits only on pass, never push.
6. **Every script**: JSON out, exit 0 pass / 1 findings / 2 error with a
   `remediation`. ASCII only, run through `eda python`.
7. **Ask in batches**, at checkpoints or when blocked.
8. **A gate that did not run is a refusal, never a pass.** A stub exits 2.
   Never weaken a check, widen a bound, or hand-edit a report or artifact.

## Phase machine (docs/design.md 1.4)

```
P0 Intake - P1 Split -
P2 The two nested runs (analog to its own release; digital to synth + H1) -
P3 Integrate + the top gates (cosim, digital harden with the macro,
   digital signoff + release, top_drc, top_lvs) -
P4 Release -[H2: sign-off]-
```

The order is fixed by one dependency: the analog side's GDS and abstract
(`analog/layout/<block>.gds`, from `layout_gen.py`) are the digital side's
LibreLane hard macro, and the digital harden produces the whole Tiny
Tapeout tile in ONE run. So the analog side releases first; the digital
side runs P1-P5 in parallel with it, then waits for integrate before it
hardens (`jobs.py start --gate harden --workspace <ws>/digital`, ten
minutes or more, detached, polled). The top gates then run on the
assembled GDS that harden produced.

Gates (`engine/reference/gates.yaml`, msde rows), in pipeline order:

| Gate | Phase | Passes when | Status |
|---|---|---|---|
| split | P1 | every crossing signal appears in both side specs with matching direction, level, domain, width and load | real (`check_split.py`) |
| cosim | P3 | every top-level measure inside its bound, the digital side toggled, the analog side ran | real (`check_cosim.py`, cocotbext-ams) |
| top_drc | P3 | 0 violations on the assembled GDS | **stub** until `check_top_drc.py` lands |
| top_lvs | P3 | the assembled GDS matches the top netlist, analog block as a subcircuit | **stub** until `check_top_lvs.py` lands |
| release | P4 | every msde gate fresh-pass AND both nested workspaces released | real (`check_release.py`) |

`release` for an msde block runs `check_release.py`'s `nested_problems()`
on top of the usual attestation: each of `digital/` and `analog/` must
hold a `state.json` of the right skill whose own `reports/checks.json`
still verifies (`attest.py verify`). A nested edit after that side
released fails here as `nested_not_released`.

## Edit classes and the nested cascade

msde's own edit classes (`invalidation.yaml`) are `spec_edit` and
`interface_edit`; both mark `split`, `cosim`, `top_drc`, `top_lvs` and
`release`. `docs/design.md` 1.6 adds that `interface_edit` marks both
nested runs' `spec_edit`, and a plain `state.py edit` cannot reach a
nested `state.json`. So after ANY interface edit, run in each nested
workspace that exists:

    ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/bin/eda python \
      ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/engine/scripts/state.py edit \
      --workspace blocks/<name>/digital --class spec_edit --note "msde interface_edit cascade"

and the same for `blocks/<name>/analog`. The `split` recipe carries both
steps. Then re-drive each side through its own router until it is fresh.

Other cross-boundary edits, declared where the file lives:

| what changed | declare | where |
|---|---|---|
| interface.yaml | `interface_edit`, then the cascade above | msde, then both sides |
| the digital harden config (integrate's macro entry) | `harden_config_edit` | `digital/` |
| the analog layout re-released | re-run `integrate` - the digital harden's macro moved | msde + `digital/` |
| `tb/` (the cosim bench) | none exists for msde; `cosim`'s `tb` input hash catches it | msde |

## Run start / resume

**New block** (`full-run`): `state.py init --workspace blocks/<name>
--skill msde --block <name>`, copy the spec into `brief/`, then the
splitter. The nested workspaces are created by their own `full-run` plans
in P2, with block names `<name>` (digital - it is the tile) and
`<name>_analog`.

**Existing block**: `state.py resume` on the msde workspace AND on each
nested one that exists, then `jobs.py status --workspace <ws>/digital
--all` (a harden may have finished or died). Re-enter at the earliest
phase any of the three reports as unfinished; never redo a gate that is
passed and fresh.

## The fix loop

The msde gates use the engine's fix loop exactly as `/vde` does (see
`skills/vde/SKILL.md`, "The fix loop"): budget (`state.py budget --path
fix_loops.<gate> --consume`), snapshot, `fix_dispatch.py`, one fixer per
work order, re-run the gate, restore on regression, escalate on budget.
What differs:

- **Every msde work order goes to `skills/msde/agents/fixer.md`**, whatever
  its `fixer` domain field says: the engine's routing table has no msde
  kinds yet, so a `split` finding says `review` and a cosim `test_failed`
  says `rtl`. The msde fixer's own table is its scope.
- **A defect inside a side is fixed inside that side.** When a fixer or
  reviewer reports one in OPEN (a wrong divide ratio, an inverted control
  word, a macro pin missing), run that side's own `fix-finding` through
  its router and its own gates, then re-run the msde gate that found it.
  `cosim` does not hash either nested workspace, so re-run it by hand.
- **`release`'s findings are re-entry, not fixes.** `nested_not_released`
  names the side; `gate_not_ready` names the msde gate.

## Digest discipline

Write `log/<phase>-digest.md` (10 lines target, 15 cap) before every
`set-phase` and before H2: numbers first - split signal count, cosim
measures against bounds, each side's phase and gate counts, open issues
per workspace.

## Human checkpoint presentation (H2, and the nested ones)

Digest + artifact paths, never raw logs: what completed, the digest, the
files to look at (`reports/checks.json` here and in each side, the gate
results), and the question with a recommended answer. Record with
`state.py human --workspace <ws> --checkpoint H2 --status
approved|rejected`. A nested side's own checkpoints (its H1, the analog
H2) are presented the same way, labelled with the side, and recorded in
that side's `state.json`.

## Agent spawn template

Every spawn contains exactly: the role prompt (`skills/msde/agents/<role>.md`),
the workspace-relative paths it needs, its assignment, "return the output
contract; do not start other phases' work", and a spawn record
(`state.py spawn --workspace <ws> --role <r> --model <m> --phase <p>`).
Nobody at msde level gets `digital/holdout/`.

| tier | roles |
|---|---|
| fable/high | splitter (the split is the hardest judgment in the run), reviewer (fresh context) |
| opus/high | integrator, fixer |

If a tier's model is unavailable, substitute the nearest one an effort
step up and record it; never silently drop a tier.

## Known limits (be honest about these)

- **What is proven is cosim with behavioral analog models.** `cosim` runs
  on cocotbext-ams, not ngspice's `d_cosim` (broken in this image -
  `docs/spikes/dcosim.md`), and the working bench (`corpus/msde/
  ring_osc_div`) simulates an `ideal` analog model. A transistor-level
  block inside the bridge has not converged reliably yet.
- **`top_drc` and `top_lvs` are stubs** in `gates.yaml` until
  `check_top_drc.py` and `check_top_lvs.py` land. They exit 2 today, so
  `set-phase P4` refuses and `release` cannot pass; a `full-run` stops
  cleanly at the end of P3.
- **The integrate mechanics are a spike in progress.** How the analog GDS
  becomes the digital harden's macro, and where the top netlist lives, is
  `docs/spikes/macro_harden.md`'s answer, not settled here.
- **`dac_spi` and `sar_adc` are specs only.** Those corpus rungs have
  interface files and `split` faults, no reference design or bench; they
  may stay red. `sensor_counted` and `ring_osc_div` are the working rungs.
- **`split` reads the two root-level side specs**, not the nested runs'
  own `spec.yaml` (`check_split.py`'s docstring). A nested spec-writer
  that drifts from `digital_spec.yaml` is caught by `cosim` or `top_lvs`,
  not by `split`. And `split` hashes only `interface.yaml`, so re-run it
  by hand after a side-spec-only edit.
- **No msde fixer routing in the engine.** `cluster_violations.FIXER_HINTS`
  has no split or cosim kinds; the playbook routes every msde order to the
  msde fixer instead (see the fix loop above).
