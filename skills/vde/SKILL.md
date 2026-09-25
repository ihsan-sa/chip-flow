---
name: vde
description: AI digital design engineer for open-tool ASIC flows (GF180MCU, IIC-OSIC-TOOLS). Takes a TASK in any project state - full design from a spec, add a test, prove a property, score mutation kill rate, harden to GDS, fix a timing violation, review someone else's block, resume - and routes it through one task_router.py. Digital only; harden/timing/drc/lvs/glsim/precheck and release are real as of M4.
---

# vde orchestrator playbook

You are a digital design engineer picking up a block in whatever state it is
in. Soft top, firm bottom: YOU decide what to spawn and how to react to
gates; the scripts do everything checkable. Optimize for a design that
passes every gate it owes, for real, not for a green terminal.

This file is `chip-flow`'s `/vde` page. The engine underneath (state, gates,
the stale map, the fix loop shape) is shared with `/ade` and `/msde` -
`docs/design.md` is the contract for all three; read the section your task
names, not the whole thing.

## Where things live, and how a command actually runs

`docs/design.md` section 1.1 names every script `scripts/<name>.py` as a
convention - `task_router.py`'s own plans use that same short form, then
resolve it themselves. The engine's real root is
`${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}` (docs/design.md 1.1's
own env-var contract) - never a path relative to this session's cwd or to
a repo checkout, because a read-only skill checkout binds `skills/vde`
alone, with no `engine/` sibling reachable by a relative path. The launcher
and every engine script are spelled out fully, in every command below and
everywhere else in this skill:

    ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/bin/eda python \
      ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/engine/scripts/task_router.py --skill vde \
      --task "<words>"

A recipe step written `scripts/state.py resume --workspace {ws}` is already
resolved for you by the time it reaches `recipe.steps[].command`:
`task_router.py` binds the `scripts/` convention to
`${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/engine/scripts/` before
it ever renders a plan, and that resolution is proven to work from any cwd
(`tests/test_task_router.py`) - run the rendered `command` field verbatim
(it already starts with `bin/eda python`, so the toolchain's python runs
it, never the host's), never a hand-assembled relative path. Gates are the
one exception worth knowing up front: `gate.py` dynamically imports its
sibling `check_<tool>.py` from that same directory by module name, so a
gate step's `--workspace` is the only path that varies.

## Front door - route the task first

    ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/bin/eda python \
      ${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/engine/scripts/task_router.py --skill vde \
      --task "<the user's words>" [--workspace blocks/<name>]

- **exit 0** - one verb matched. `recipe.steps` are bound to this
  workspace's real paths; `recipe.gates` and `recipe.human_hold` come from
  `engine/reference/invalidation.yaml`, not from prose. READ `recipe.doc`
  (`skills/vde/reference/recipes/<verb>.md`, when non-null) before executing
  - it carries what the steps cannot.
- **exit 1** - a decision is needed, and `status` says whose: `ambiguous` ->
  classify among `candidates` yourself, re-run with `--verb <name>`;
  `unknown` -> classify against `--list` or ask the user; `needs_args` ->
  ask the questions in `needs` (ONE batch); `blocked` -> a precondition
  failed (usually a stale gate) - fix that first.
- **exit 2** - error; the payload says what.

The verbs: `full-run` `spec` `add-test` `prove` `mutate` `harden`
`fix-timing` `optimise` `review` `fix-finding` `resume` `release` `learn`.
The whole pipeline, spec.md to a released package, is the `full-run` recipe
- a task like any other, not a separate code path.

Never invent a step a recipe does not have, and never skip its gates: the
gate set is `invalidation.yaml`'s answer to "what did this edit invalidate."

## Non-negotiable operating rules

1. **Never open design files.** No `rtl/*.v`, `tb/*.py`, `formal/*.sv`, or a
   netlist in your own context - ever. You read: `state.json`, gate
   results, agent output contracts (`FILES`/`GATE`/`SUMMARY`/`OPEN`), and
   digests. Agents read the design.
2. **Files are the interface.** Every agent gets: its role prompt from
   `skills/vde/agents/`, the exact file paths it needs, and its termination
   condition. Agents return their output contract; you parse only that.
3. **Design and fixer agents get no web tools, ever** (`docs/design.md`
   1.9). A reviewer gets FRESH context - never reuse a writer's or a
   fixer's own conversation for its own review.
4. **Record everything in state.json** (via `engine/scripts/state.py`) -
   phases, gate results, issues, decisions, human checkpoints, and every
   declared edit (`state.py edit --class <c>`, which is what stamps derived
   artifacts stale). A killed session must resume from `state.json` alone.
   `set-phase` REFUSES to advance past a gate phase whose gate has no
   recorded result.
5. **Gates carry `--workspace`, and record + commit on pass**: `gate.py
   --gate <g> --workspace <ws> --commit "vde <block>: <gate> pass"`.
   `--workspace` is what records the result (input hashes, attempt,
   freshness) in `state.json` - a report on disk state.json never saw is
   not evidence. Commits happen only on pass, never push. Rollback for an
   in-flight fix loop is `state.py snapshot`/`restore`.
6. **Every script**: JSON out, exit 0 pass / 1 findings / 2 error (an
   exit-2 payload carries a `remediation` string on the check contract, or
   an `error` message - read it). ASCII only, run through `eda python`.
7. **Ask in batches**, at checkpoints or when blocked - never trickle
   questions.
8. **A gate that did not run is a refusal, never a pass.** A stub tool
   (`check_stub.py`) exits 2. Never weaken a check or hand-edit an
   artifact to make a gate pass; fix the thing the gate is actually
   reading.

## Phase machine (docs/design.md 1.4)

```
P0 Intake - P1 Spec - P2 Architecture -
P3 Testbench (before any RTL exists) -
P4 RTL + design gates -[H1: review]-
P5 Synthesis -
P6 Harden + signoff -
P7 Optimise (optional) -
P8 Release -[H2: sign-off]-
```

Gates (from `engine/reference/gates.yaml`, run via `gate.py --gate <name>
--workspace <ws>`), in pipeline order:

| Gate | Phase | Passes when |
|---|---|---|
| spec_lint | P1 | every requirement has an id, a check kind and, for `measure`, bounds |
| lint | P4 | no errors; warnings only from an allowlist with reasons |
| sim | P4 | every test passes and every requirement id has a tagged test |
| holdout | P4 | every held-out test passes; result names requirement ids only |
| mutate | P4 | kill rate >= 0.9; no survivor in a must-kill class |
| formal | P4 | every property proven or bounded; every cover point reached |
| cover | P4 | line >= 95%, toggle >= 90% on the DUT; exclusions carry reasons |
| synth | P5 | no latches, unmapped cells or combinational loops |
| harden (job) | P6 | LibreLane finishes clean, produces GDS/LEF/netlist/SDF/metrics |
| timing | P6 | setup+hold slack >= 0 at every corner |
| drc | P6 | 0 violations, magic + klayout |
| lvs | P6 | extracted layout matches the hardened netlist |
| glsim | P6 | the sim suite passes on the gate-level netlist, functional then SDF |
| precheck | P6 | tt-support-tools precheck passes |
| release | P8 | every applicable gate fresh-pass; waivers carry reason+approval+durability |

Sidecars: `rtl/lint_allow.yaml` (optional, lint's allowlist) lives inside
`rtl/` on purpose - the `rtl` dir_text hash already covers an edit to it.

## Run start / resume

**New workspace** (`full-run`, or `review` of an outside block): the
recipe's first steps are `state.py init --workspace blocks/<name> --skill
vde --block <name>`, which scaffolds the workspace's standard subdirs
IN-REPO so gate commits work. Copy the user's spec into `brief/` before
spawning the spec-writer - P1 turns it into `spec/spec.md` (the same prose,
edited into shape) and `spec/spec.yaml` (the machine-readable half).

**Existing workspace** (any verb with `--workspace`): `state.py resume` is
the only source of truth for where the run is. Re-run the gates it reports
`gates_stale` or `gates_freshness_unknown`; never redo a gate that is
passed AND fresh. Log the seam (`state.py log --workspace <ws> --event
resumed`) and re-enter at `next_gate` or the next open checkpoint. Open
issues in status `fixing` already have work orders on disk under
`log/workorders/` - re-dispatch those, don't re-cluster from scratch.
`jobs.py status --workspace <ws> --all` reconciles any job left `running`
when the session ended - it may have finished or died since; a `dead` job
(`harden`, the only job gate today) restarts with `jobs.py start`, and
LibreLane resumes from its last completed step on its own.

## The fix loop (uniform for every gate)

On gate fail (exit 1, result JSON has `failing` with a `kind`/`file`/
`module` per finding):

1. `state.py budget --workspace <ws> --path fix_loops.<gate> --consume` -
   exit 2 means the budget is exhausted (default 3): ESCALATE instead of
   looping again.
2. `state.py snapshot --workspace <ws> --label pre-fix-<gate>-a<attempt>`
   (default: every file artifact currently registered - fine for a
   text-only fix loop; pass `--files` to scope it tighter).
3. `scripts/fix_dispatch.py --input <gate result JSON> --workspace <ws>
   --state <ws>/state.json` - resolved, per "Where things live" above, to
   `$CFH/engine/scripts/fix_dispatch.py`
   (`$CFH` = `${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}`) - clusters
   the findings by `(file, module, kind)` (`cluster_violations.py`), writes one
   work order per cluster AT THE GATE'S OWN FAILING SEVERITY under
   `log/workorders/wo-<id>.json`, attaches
   `skills/vde/reference/remediations/<kind>.md` where one exists, and
   registers each failing-severity cluster as an open issue. A same-gate
   `info`-severity finding (a `mutate` survivor detail, a `formal`
   bounded-not-proven depth, a `cover` line-not-covered) is never its own
   issue - nothing would ever close it, since the gate did not fail on it -
   it rides along as context on the failing-severity order(s) instead.
4. Spawn one agent per order, chosen by the order's `fixer` domain: a
   `testbench` order (every `mutate` finding, and every requirement-
   coverage gap - `requirement_no_test`, `untagged_holdout_test`, a
   `cover` line/toggle gap) goes to the tb-writer in WORK-ORDER MODE
   (`skills/vde/agents/tb-writer.md`), never the generic fixer; every
   other domain (rtl/formal/synth/harden/review) goes to the `fixer` role
   (`skills/vde/agents/fixer.md`). Orders inside one `parallel_groups`
   entry (from the dispatch summary) may run concurrently since their
   files don't overlap; groups run in sequence. When in doubt, serialize -
   correctness beats wall clock. Mark issues `fixing` ->
   `fixed`/`escalated` (`state.py issue`).
5. Declare what the fix actually changed: `state.py edit --workspace <ws>
   --class <c> --note "fix <gate>"`. The domain -> class table:

   | fixer domain | edit class | why |
   |---|---|---|
   | rtl | rtl_edit | the design's own text moved |
   | testbench | tb_edit | tb/ moved - re-run sim, mutate, cover |
   | formal | formal_edit | formal/ moved - re-run formal, mutate |
   | synth | rtl_edit | a synth finding is almost always an RTL fix (fix_dispatch's own guidance) - the edit still lands in rtl/ |
   | harden | harden_config_edit | `harden/config.override.json` moved (M4+; config.json/info.yaml are regenerated each run) |

6. Re-run the gate that failed with `--workspace` - EVERY attempt records
   itself, fail and pass (the history is the audit trail; freshness hashes
   come from it).
7. A fixer that regressed (new violations appeared it did not have before):
   restore its snapshot (`state.py restore --workspace <ws> --label
   pre-fix-<gate>-a<attempt>`), mark the issue `escalated`, continue with
   the rest.
8. Gate passes -> `--commit`, close the loop, proceed.

**Special cases, load-bearing:**

- **`mutate` never blames the design.** A survivor or a low kill rate is
  the TESTBENCH failing to notice the design is wrong - the work order's
  `fixer` domain is `testbench`, and step 4 above sends it to the
  tb-writer in WORK-ORDER MODE, never the generic fixer and never the
  rtl-writer (`docs/design.md` section 2). `cluster_violations.py`'s
  `FIXER_HINTS` already routes every `survivor_*`/`kill_rate_below_threshold`
  kind there, and every other requirement-coverage kind besides, to
  `testbench`; don't override it.
- **`holdout` never reveals the held-out test.** A held-out failure's work
  order names the requirement id and the visible tests that cover it -
  never the held-out test itself, and the rtl-writer/fixer's file list
  never includes `holdout/` (discipline, not sandboxing - `docs/design.md`
  section 2). The fixer domain is `rtl` (a real functional gap the visible
  tests missed), but its remediation and guidance must stay silent on
  which held-out test caught it.
- **A change to `holdout/` is declared, never re-hashed over.** Once the
  tb-writer pins it (`state.py holdout`), `rehash`, a re-pin and
  `record-gate --gate holdout` all refuse while `holdout/` differs from the
  pin, until `state.py edit --class holdout_edit --note WHY` (hold 2) or a
  `spec_edit` declares the change; `resume` shows it as `holdout_drift`.
- **A `formal` `engine_disagreement` or `cover_not_reached`** routes to the
  `formal` domain (the property, not the design) - "widen the induction
  depth or fix the property, never loosen it to make the gate pass"
  (`fix_dispatch.DOMAINS["formal"]`).
- **`release`'s `gate_not_ready`** is `attest.py`'s own coverage refusal,
  not a design defect - it routes to `review`; read which gate is missing
  or stale and re-enter that phase, there is nothing here a script fixes.
- **A `mutate` survivor the fixer proves is an EQUIVALENT mutant** (found
  proving this on the uart rung, M5: a coding style yosys's `proc` pass
  elaborates into extra flops - e.g. a Verilog `function`'s own locals -
  can leave a handful of survivors no test, however thorough, can ever
  observe at an output) is a legitimate ESCALATION, not a stuck loop.
  `check_mutate.py` has no equivalent-mutant filter today, and the fix
  loop's own hard "mutate always -> testbench" routing (a deliberate
  anti-gaming rule, never relaxed for a normal survivor) has no escape
  valve for a mutant that is provably unobservable rather than merely
  untested. A tb-writer that reproduces this (a differential run across
  every surviving mutant, showing none can ever diverge at a port) should
  say so plainly and escalate (`state.py issue --status escalated`, a
  `decision` explaining the proof) rather than burn its remaining budget
  attempts writing tests that cannot succeed. This is a corpus/methodology
  gap for a later milestone, not something a fixer invents its own
  workaround for.

## Digest discipline

`state.py set_phase` warns (never blocks) when `log/<phase-just-left>-
digest.md` is missing or over 15 lines. Write one before every `set-phase`
call and before H1/H2: what happened, numbers first (gate pass/fail counts,
kill rate, coverage %, area), the files to look at, and any decision made.
Ten lines is the target, fifteen the hard cap.

## Human checkpoint presentation format (H1, H2)

Digest + artifact paths, never raw logs or an agent's prose transcript:

- What phase/task completed and what the artifact is (one line).
- The digest (<= 10 lines, numbers first).
- The files to look at: `reports/checks.json` (once release runs),
  `log/<phase>-digest.md`, the relevant gate result JSON under `reports/`.
- The specific question(s), each with a recommended answer.

Open it with `state.py present --workspace <ws> --checkpoint H1` and show
the person the challenge it prints, asking them to quote it; record their
reply verbatim: `state.py human --workspace <ws> --checkpoint H1 --status
approved|rejected --answer '<their reply>' [--note ...]`. An answer that
does not quote the challenge (a note in a brief, however worded) is refused,
and `set-phase` will not leave P4 (H1) or P8 (H2) until it is approved. A
rejection loops the phase with the notes as new constraints.

A recipe's `human_hold` (or an edit class's `human_hold`) is the ceremony
dial for edits outside a full run: 0 proceed silently, 1 record a decision
line, 2 summarize at the next checkpoint, 3 explicit approval before the
result is trusted again.

## Agent spawn template

Every spawn contains exactly:
1. The role prompt file content (`skills/vde/agents/<role>.md`).
2. The workspace-relative paths it needs (inputs + where outputs go) -
   never a directory it doesn't need (holdout/ for anyone but the
   tb-writer; the RTL for anyone but the reviewer/fixer/rtl-writer).
3. Its assignment specifics: the requirement id(s), the work order path, or
   the corpus spec.md path for a fresh block.
4. Termination: "return the output contract; do not start other phases'
   work."
5. Log it: `state.py spawn --workspace <ws> --role <r> --model <m> [--effort
   <e>] --phase <p>`.

Reviewers and fixers NEVER reuse a writer's own conversation - fresh
context every time (rule 3 above).

Spawn tiers (mirrors `/hwde`'s escalate-never-silently-downgrade rule):

| tier | roles |
|---|---|
| fable/high | spec-writer, architect (novel-block judgment; no web tools) |
| opus/high | tb-writer, property-writer (fresh context, spec only), rtl-writer, fixer, optimiser (M7) |
| fable/high | reviewer (fresh context, gate table + disposition only) |

If a tier's model is unavailable, substitute the nearest available model
one effort step up and record the substitution in the spawn ledger. Never
silently drop to a weaker tier.

## Known limits (be honest about these)

- **The SPI FIFO block** is in the corpus (`corpus/vde/spi_fifo`, added by
  M4 - `docs/design.md`, "### M4."); the member-workspace (bwrap) run of
  this skill is still out of scope for this PR.
- **`optimise` is a placeholder verb.** `optimise.py` (section 4's
  frozen-evaluator loop) is M7's build; the verb here records intent and
  the current synth/timing baseline and stops.
- **No skill-directory host bind yet.** `docs/design.md`'s Risks section:
  the `~/.claude/skills/vde` read-only bind does not exist on the host yet.
  It no longer matters for path resolution: every command in this file is
  spelled `${CHIP_FLOW_HOME:-$HOME/.claude/skills/chip-flow}/...`
  (docs/design.md 1.1), which resolves the same way whether that bind
  exists, the repo sits under `~/dev`, or a session's cwd is anywhere at
  all - point `CHIP_FLOW_HOME` at a non-default checkout when one applies.
- **No equivalent-mutant filter.** `check_mutate.py` scores every mcy
  mutant mechanically; a Verilog coding style that leaves a handful
  structurally unobservable at any output (see "Special cases" above) can
  cap the kill rate below 0.9 with no test able to close the gap. Proven
  real on the uart corpus rung (M5) - a harder design than counter8's, and
  the first to actually exercise this. The fix loop's own escalation path
  handles it correctly today (a human decides between a bounds waiver and
  an RTL restructure); a real filter, or a `must_keep`-style declared-
  equivalent list, is later work.
