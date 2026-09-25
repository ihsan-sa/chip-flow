# chip-flow design

Three Claude Code skills for designing chips with open tools, on one shared engine. `/vde` designs digital blocks in Verilog, `/ade` designs analog blocks, and `/msde` takes a mixed-signal task, splits it, and drives the other two. Each takes a task wherever a project stands, and the full run from a written spec to a layout ready to submit is one of those tasks.

The method is `/hwde`'s, a PCB skill hardened over three full board runs: one folder per block with a `state.json` a killed session resumes from, a task router in front, agents that do the design work and scripts that do every check, gates that refuse until every check that applies has run, a map of what an edit makes stale, a fix loop with a budget, and reference designs with planted faults that each gate has to catch. What is new is everything under it: the tools, the checks, the design knowledge, and four things a board flow never needed. Silicon takes most of a year to come back, so the flow has to test its own testbenches. Hardening runs for an hour, so a gate becomes a job. Analog layout has no autorouter, so the agent writes code that generates it. And the PDK is a pinned dependency.

Fixed before this document: the PDK is GF180MCU (gf180mcuD), digital comes first, the first milestone aims at a Tiny Tapeout tile ready to submit, the tools come from the IIC-OSIC-TOOLS 2026.09 image unpacked at `~/.cc/toolchains/iic-osic-tools-2026.09`, and everything lives in this one repo.

## 1. Architecture

### 1.1 Layout, and how a skill finds the engine

```
chip-flow/
  bin/eda                    the tool launcher (1.2)
  SKILL.md                   the engine's page: eda, check_env, and that design work goes to /vde /ade /msde
  engine/scripts/            state.py gate.py task_router.py fix_dispatch.py jobs.py faults.py optimise.py
                             bench.py attest.py release.py check_env.py check_<gate>.py ...
  engine/lib/                checklib safelib statelib benchlib simlib speclib
  engine/reference/          gates.yaml invalidation.yaml tasks.yaml corners.yaml tt/ (the TT template, pinned)
  skills/{vde,ade,msde}/     SKILL.md agents/ reference/{tasks.yaml,recipes/,remediations/,topologies/} templates/
  corpus/<skill>/<design>/   spec.md spec.yaml reference solution faults/ holdout/
  evals/                     ladder.py fixtures/ cvdp/ results/ ladder.md
  tests/                     pytest, run as eda python -m pytest
```

Sessions see the repo as `~/.claude/skills/chip-flow` and the three skill directories as `~/.claude/skills/vde`, `ade` and `msde`, read-only binds of the subdirectories, so a skill never reaches the engine by a relative path. Every script under `skills/` resolves it as `Path(os.environ.get("CHIP_FLOW_HOME", "~/.claude/skills/chip-flow")).expanduser()`; the variable exists so tests and a checkout under `~/dev` work.

Every script runs as `eda python <script>`. One interpreter, the image's, avoids the two-pythons trap that cost `/hwde` a step, and it is the one with cocotb, LibreLane, klayout and gdsfactory in it. Scripts keep `/hwde`'s contract: argparse, JSON to stdout or `--out`, exit 0 pass, 1 findings, 2 error with a `remediation` string, no prompts, ASCII.

### 1.2 The launcher

Sessions run under bwrap with no docker and no nested namespaces, so the image cannot be entered; `bin/eda` runs it unpacked instead. `EDA_TOOLCHAIN` picks the tree, default `$HOME/.cc/toolchains/iic-osic-tools-2026.09` - the box's own default, spelled out in exactly one place, `bin/eda`'s own `T=` line; anything else that needs the resolved path (`tests/check.sh`, `check_env.py`) asks for it with `eda --print-toolchain-root` rather than carrying a second copy that could go stale on its own. Every binary in the tree is built against the image's glibc 2.39, which does not mix with this host's newer one, so `eda <tool> [args]` never lets the kernel resolve a binary's own interpreter: it execs everything - the tool asked for, and anything that tool spawns - through the image's own loader, `$T/lib64/ld-linux-x86-64.so.2 --library-path <image lib dirs>`. Each tool gets whatever narrow environment its own invocation needs (`magic` and `netgen` get `PDK_ROOT`/`CAD_ROOT`/`TCL_LIBRARY`; `librelane` additionally gets `TOOLS` and `RUBYLIB`; `klayout` gets `RUBYLIB`) - there is no single global environment block modeling the image's own profile script, because no case here needs one wholesale.

Tools spawn tools by bare name: LibreLane starts openroad, yosys, magic, klayout, netgen and (for one DRC check) ruby; sby starts yosys, yices and yosys-smtbmc; cocotb starts iverilog and vvp by name too; verilator's own build spawns the host's make and g++, and gcc's driver resolves cc1/cc1plus/as/ld itself, relative to its own invoked path, so those get `-B<shim dir>` injected rather than a PATH fix. Every one of those is a small wrapper script that re-execs the real binary through the loader above, generated once into a shim directory `eda` puts first on `PATH` - a cache, not a per-run temp directory, keyed on the toolchain path and this script's own content (editing `bin/eda` or pointing at a different image produces a different cache entry) and built atomically (a private temp directory, `mv -T` into place; a losing race between two cold `eda` calls just deletes its own copy and uses the winner's). Two tools need more than a loader wrapper and recurse back into `eda` itself instead (magic and netgen, whose correct invocation is specific env plus a stdin script, kept in their own case here rather than duplicated in a shim); that shim execs a COPY of this script cached alongside the others, never the invoking checkout's own path, so a cache entry never depends on one worktree still existing at that path later. WHERE the cache lives differs by who is running: this box's worker and member sandboxes (`CC_WORKER_SANDBOX=1` / `CC_MEMBER_SANDBOX=1`) get `~/.cache`, fast and persistent across that sandbox's own runs; every other - unsandboxed, host - run gets `~/.local/state` instead, because a worker sandbox on this box binds the host's real `~/.cache` read-write, so a cache under it is one a worker running a foreign PR could plant a shim into that a later, unsandboxed host run would then execute unmodified.

Python entry points with an absolute `#!/usr/bin/python3` shebang (`librelane`, `mcy`, the tt precheck) are handed straight to the image's own `python3.12` through the loader - `eda python <script> [args]` - never through the shebang the kernel would otherwise read, because the host's own `/usr/bin/python3` is a different interpreter entirely; ones with `#!/usr/bin/env python3` need no such handling; they just find the shim directory's own `python3` first on `PATH`. `PYTHONHOME` is exported globally, not only for `eda python` itself, because several tools (yosys among them) embed a Python interpreter and call it unconditionally; `PYTHONNOUSERSITE=1` is set everywhere a script runs so a stray host `~/.local/lib/python3.12/site-packages` can never shadow the image's own, glibc-2.39-built one. `eda check-env` runs `check_env.py`: every tool's resolved path and a version obtained by actually running it (not just a stat), the PDK version read from ciel's `current` file, an mcy presence check (mcy ships under a path this launcher's generic fallback cannot re-root on this host - `check_env.py` explains why), and the combined python-import smoke (`cocotb`, `librelane`, `klayout`, `gdsfactory`) M0 names.

### 1.3 Engine modules

Ported from `/hwde` with the domain swapped: `state.py` and `statelib` (state, freshness, snapshots, locks), `safelib` and `checklib`, `task_router.py` and the `tasks.yaml` format, `gate.py` (evaluate, record, commit on pass, waivers, strict mode), `fix_dispatch.py` and `cluster_violations.py` (the clustering key becomes file, module and finding kind), `attest.py`, `bench.py` and `benchlib`, `learnings.py`, `sim_run.py` and `simlib` (ngspice benches with `.bounds.json` sidecars, now through `eda ngspice -b`).

New: `eda`, `check_env.py`, `jobs.py`, `faults.py` (corpus faults and per-run mutants), `holdout.py`, `optimise.py`, `release.py`, `speclib`, `corners.py`, `layout_gen.py`, `cosim_run.py`, and one `check_<gate>.py` per gate. `state_migrate.py`, `report_gen.py` and everything KiCad-specific do not port.

### 1.4 Workspace and state

A block is `blocks/<name>/` in the project's repo, so gate commits work. `state.py init` makes `brief/`, `spec/` (`spec.md` and `spec.yaml`), `rtl/` or `netlist/`, `tb/`, `holdout/`, `formal/`, `synth/`, `harden/` (the TT project: `info.yaml`, `config.json`, generated `tt_um_<name>.v`), `layout/`, `sizing/`, `optimise/`, `reports/`, `log/` and `state_snapshots/`. LibreLane runs go under `runs/`, gitignored.

`spec.yaml` is the machine-readable half of the spec, and `spec_lint` refuses a block without one. It carries `requirements: [{id, text, check: sim|formal|both|measure, bounds?}]`, `ports` and a `tt_pins` map, `clock: {period_ns, domains}`, `must_keep` (cells an optimiser may not remove, such as a hand-instantiated ring), and for analog blocks `supply`, `corners`, `measures` with per-corner bounds, and `post_layout_bounds`.

`state.json` is schema version 3, `/hwde`'s v2 with these additions:

```
{ "version": 3, "skill": "vde|ade|msde", "block": str, "phase": "P0".."P8"|"done",
  "toolchain": {"image": path, "versions_sha": ..., "pdk": "gf180mcuD", "pdk_sha": ...},
  "gates": {name: {status, attempts, last: {ts, status, counts, inputs: {kind: "<norm>:<sha>"}, job?}, history[], stale?}},
  "jobs": {id: {gate, pid, started, finished?, status: running|done|dead, log, result?}},
  "holdout": {"sha", "written_by", "ts"},
  "optimise": {"trials": path, "evaluator_sha", "best": {trial, score}},
  "human", "artifacts", "open_issues", "budgets", "decisions", "edits", "spawns", "history": as v2 }
```

Everything `/hwde` recorded stays: every mutating command appends to `history`, writes are atomic under a lock, `set-phase` refuses to pass a gate phase with no recorded result, snapshots are contained and transactional.

Phases. Digital: P0 intake, P1 spec, P2 architecture, P3 testbench (before any RTL exists), P4 RTL and the design gates, H1 review, P5 synthesis, P6 harden and signoff, P7 optimise (optional), P8 release with H2 sign-off. Analog: P0 intake, P1 spec, P2 topology, P3 bench, P4 sizing and the simulation gates, H1, P5 layout and its gates, H2, P6 release. Mixed-signal: P0 intake, P1 split, P2 the two nested runs, P3 integrate and the top gates, P4 release with H2. Submitting to a shuttle is always the person's.

### 1.5 Gates

Every gate is a row in `gates.yaml` with `skill`, `phase`, `tool` (its `check_<gate>.py`), `fail_severities`, `max_count`, `strict`, `job: true` where it runs as a job, and `fault`, the corpus fault that proves it. `gate.py --gate <g> --workspace <ws>` runs the tool, evaluates, records pass and fail, and commits on pass. A gate whose tool could not run is exit 2, never a pass.

Digital (`/vde`):

| gate | tool | passes when | planted fault it must catch |
|---|---|---|---|
| spec_lint | speclib | every requirement has an id, a check kind and, for `measure`, bounds; tt_pins fit the tile | a requirement with no way to check it |
| lint | verilator --lint-only -Wall | no errors; warnings only from an allowlist with reasons | an `always @*` missing an else (latch) |
| sim | cocotb 2 on Icarus | every test passes and every requirement id has a test tagged with it | counter wraps one early |
| holdout | cocotb on `holdout/` | every held-out test passes; the result names requirement ids only | UART parity inverted where the visible tests do not look |
| mutate | yosys mutate via mcy, fixed seed, N mutants | kill rate at or above 0.9; no survivor in the must-kill classes (reset removed, output stuck, condition inverted) | a testbench that asserts nothing |
| formal | SymbiYosys: smtbmc k-induction and abc pdr, plus cover | every property proven, or bounded to at least the depth the spec asks; every cover reached | a register with no reset: sim passes, formal fails |
| cover | verilator --coverage, same tests | line at or above 95%, toggle at or above 90% on the DUT; exclusions carry reasons | an unreachable state |
| synth | yosys synth to the gf180 liberty | no latches, unmapped cells or combinational loops; area recorded | a combinational loop |
| harden (job) | LibreLane 3, TT config, gf180mcuD | the flow finishes with no failing step and produces GDS, LEF, netlist, SDF, metrics | a design too large for the tile |
| timing | OpenSTA on the hardened netlist at LibreLane's corners | setup and hold slack at or above 0 at every corner; no fanout or slew violations | a chain that misses the spec's clock |
| drc | magic DRC and klayout DRC, PDK decks | 0 violations from both | a metal spacing violation planted in the GDS |
| lvs | netgen, magic-extracted layout versus the hardened netlist | match | one net split in two in the extracted layout netlist, the electrical effect of a missing via |
| glsim | the sim suite on the gate-level netlist, then with SDF | every test passes both ways | a register read before it is written, which only X-propagation exposes |
| precheck | tt-support-tools precheck, pinned | passes | a wrong top module name in info.yaml |
| release | attest, strict | every applicable gate has a fresh recorded pass on the current inputs; waivers carry reason, approval and durability | an RTL edit after harden, which must refuse |

Analog (`/ade`):

| gate | tool | passes when | planted fault it must catch |
|---|---|---|---|
| spec_lint | speclib | every measure has bounds and a corner set; supply and devices declared | a measure without bounds |
| netlist_lint | ngspice dry run | every device uses a gf180mcu_fd_pr model, no floating node, subcircuit pins match the spec | a model not in the PDK |
| sim_tt | sim_run at typical | every `.measure` inside its bound | W and L swapped on a mirror's output device |
| sim_pvt | sim_run over the corner set | every measure inside its bound at every corner | meets at typical, loses headroom at slow and hot |
| bench_strength | faults.py device mutants (size doubled, connection removed, type flipped, bias halved) | every mutant pushes a measure out of bounds | bounds wide enough to pass anything |
| mc | ngspice Monte Carlo, only when the spec asks | yield at or above the spec | not applicable by default |
| drc | magic and klayout | 0 | a planted spacing violation |
| lvs | netgen, extracted layout versus the sized netlist | match | a device sized differently in the generator than in the netlist |
| pex_sim | magic ext2spice with parasitics (klayout_pex second), then the bench at typical and the worst corner | every measure inside `post_layout_bounds` | an output routed on a long minimum-width metal1 line |
| release | attest, strict | as digital | as digital |

Mixed-signal (`/msde`):

| gate | tool | passes when | planted fault it must catch |
|---|---|---|---|
| split | speclib over interface.yaml | every crossing signal appears in both specs with matching direction, level and domain | a control word width that differs between the two |
| cosim | ngspice `d_cosim` running the Icarus-compiled digital side inside the analog bench | every top-level measure inside its bound | control word polarity inverted |
| top_drc | magic and klayout on the assembled GDS | 0 | a planted spacing violation |
| top_lvs | netgen on the assembled GDS, the analog block as a subcircuit | match | a macro pin left unconnected |
| release | attest, strict | both nested runs released, top gates fresh | a stale nested release |

### 1.6 The stale map

`invalidation.yaml` keeps `/hwde`'s two layers. Every gate records a normalized hash of each input it read, and any change to an input invalidates the result whether or not anyone declared it. On top, `state.py edit --class <c>` marks the gates an edit class must re-establish, which catches derived artifacts whose files have not changed yet (a GDS after an RTL edit). Normalizers: `text_eol` for HDL, SPICE, SDC and generator code (a comment edit re-runs gates, and that is accepted), `json_canonical` for specs and configs, `dir_text` for test directories, `gds_geometry` (the layout read through klayout's python module and hashed without timestamps).

Edit classes: `spec_edit` marks everything including holdout, which the testbench writer must rewrite, hold 2. `rtl_edit` marks lint, sim, holdout, mutate, formal, cover, synth, harden, timing, drc, lvs, glsim, precheck, release, hold 1. `tb_edit` marks sim, mutate, cover; `formal_edit` marks formal, mutate; hold 1. `harden_config_edit` (`harden/config.override.json`, the design's LibreLane keys merged last into the regenerated config.json) marks harden onward, hold 2. `netlist_edit` marks every analog gate; `sizing_edit` marks sim_tt, sim_pvt, bench_strength, mc, lvs, pex_sim, release; `layout_code_edit` marks drc, lvs, pex_sim, release; `interface_edit` marks the top gates and both nested runs' spec_edit, hold 2.

### 1.7 Jobs

A gate with `job: true` is started by `jobs.py start --gate harden --workspace <ws>`, which runs `gate.py` detached under `setsid nohup`, records `{pid, log}` in `state.jobs` and returns. `gate.py` records the result when it finishes, exactly as a foreground gate does, and `jobs.py status` reports running, done or dead from the pid. `state.py resume` lists unfinished jobs; a session that finds one dead restarts it, and LibreLane resumes from its last completed step. A job cannot outlive the sandbox it started in, and the design accepts that.

### 1.8 Router verbs

One `task_router.py`, called with `--skill`. It loads the engine's shared verbs, then the skill's `tasks.yaml`, where the skill's entry wins. Verbs match by regex; a tie or a miss exits 1 with candidates for the session to classify and re-enter with `--verb`. Gates and human holds come from the edit class in `invalidation.yaml`, never restated in the verb.

- Shared: `full-run`, `review` (import an outside project or re-establish every gate, then a fresh-context reviewer), `fix-finding`, `resume`, `release`, `learn`.
- `/vde`: `spec`, `add-test`, `prove`, `mutate`, `harden`, `fix-timing`, `optimise`.
- `/ade`: `spec`, `resize`, `add-corner`, `layout`, `optimise`.
- `/msde`: `split`, `integrate`, `cosim`.

### 1.9 Agents

The orchestrator never opens a design file. It reads `state.json`, gate results and agents' output contracts (`FILES`, `GATE`, `SUMMARY`, `OPEN`). Roles, in `skills/<s>/agents/`:

- `/vde`: spec-writer, architect, tb-writer and property-writer (fresh context, from the spec only, before RTL exists), rtl-writer, reviewer (fresh context), fixer (one work order, allowed files listed), optimiser.
- `/ade`: spec-writer, bench-writer (fresh context, from the spec), analog-designer (topology, netlist, initial sizes and bounds), layout-writer (generator code, never a GDS), reviewer, fixer.
- `/msde`: splitter (interface.yaml and two specs), integrator (macro config, top netlist, cosim bench), reviewer, fixer.

Design and fixer agents get no web tools. A reviewer never reuses a writer's conversation.

## 2. Valid testing

The proposal cites the failure this section exists for: of code that passed one benchmark's testbenches, 44 percent then failed formal. Tests that pass too easily are worse than none, because the loop finishes and the answer is wrong. Six things together make a testbench worth trusting here.

Tests come before the design, from a different agent. At P3 the tb-writer and property-writer read the spec and nothing else, in fresh context, and write the cocotb tests, a Python reference model the tests score the DUT against (transaction by transaction for a UART, cycle by cycle for a counter), the formal properties and the held-out tests. The rtl-writer at P4 sees `tb/` and `formal/` and is not given `holdout/`. Every test carries the requirement ids it covers, so `sim` refuses a requirement no test touches.

Held-out tests exist at two strengths. In a run, `holdout/` is hashed into `state.holdout` at creation and left out of every design and fixer spawn's file list; that is discipline, not isolation, and a held-out failure produces a work order naming the requirement id and the visible tests for it, never the held-out test. In the evals, the corpus keeps its own `holdout/` outside any workspace and `ladder.py` runs it after the agent's run has ended, which is the strong form.

Mutation testing scores the testbench. `mutate` uses yosys's `mutate` pass through mcy to make N mutants of the RTL with a fixed seed (inverted conditions, stuck outputs, removed resets, off-by-one constants, swapped operators) and runs the visible tests and the bounded formal on each. A mutant no test and no property fails has survived, and the gate lists survivors by class. This is what fails a testbench that asserts nothing, and a `mutate` failure goes back to the tb-writer, not the rtl-writer. Analog has the same idea in `bench_strength`.

Formal finds what simulation skips. `formal` runs SymbiYosys with two engines, k-induction through smtbmc for a full proof and abc pdr as the second opinion, plus cover points for the states the spec names. A property that only reaches a bounded depth is recorded as bounded with its depth, and the gate never reports it as proven.

Coverage says what the tests never reached. `cover` runs the same tests under Verilator with line and toggle coverage and refuses below the thresholds; exclusions need a reason in `spec.yaml`. Requirement coverage comes from the test tags.

The record of what ran is the release. `attest.py` walks every gate that applies, checks each has a recorded pass whose input hashes match the files now, and writes `reports/checks.json`: gate, tool and version, inputs and hashes, result, timestamp, and for every gate that did not apply, the reason and who approved it. The package a person signs off carries that file, and the run's summary is generated from it, not from an agent's prose. A gate that could not run is a refusal, which answers the silent-failure class the proposal cites. Gate-level simulation and the shuttle's precheck then close the loop on the hardened netlist, because a design can be right in RTL and wrong after synthesis.

## 3. Evals

Public benchmarks say how the agents compare with everyone else's; only the flow's own corpus says whether a finished design can be trusted. Three instruments, all scripted, all writing dated JSON under `evals/results/`, with `evals/ladder.md` regenerated from them and committed.

The corpus ladder is Figure 2 of the proposal, harder upward. `/vde`: 8-bit counter, UART, SPI peripheral with a FIFO, small RISC-V core. `/ade`: current mirror, R2R DAC, comparator, bandgap. `/msde`: a sensor counted (ring oscillator into a counter), ring oscillator with a divider, DAC with an SPI register, SAR ADC. Each rung has `spec.md`, `spec.yaml`, a reference solution, `faults/` (a manifest in the `/hwde` golden style: fault, the script that plants it, the gate that must catch it, what the finding must say) and `holdout/`. `faults.py --skill vde` plants every fault into the reference solution and runs its gate; the suite passes when every fault is caught with the expected finding and the untouched reference passes every gate. That proves the gates. `ladder.py --skill vde --rung uart --run <ws>` scores the skill: a session runs `full-run` from `spec.md` alone in a fresh workspace, and the script records whether every gate went green with no hand edits, the held-out result, the kill rate, area and slack, tokens, cost, wall time and fix attempts. A rung counts when every gate is green and the held-out tests pass.

Per-stage benches on frozen inputs port `bench.py`. A fixture under `evals/fixtures/<stage>/<name>/` is a workspace snapshot at a stage boundary, every file sha-pinned, and a bench runs that stage's scripts on it and scores deterministic metrics: P3 the kill rate and requirement coverage of a frozen testbench, P4 the gate results on frozen RTL, P6 area, slack, DRC count and LVS on a frozen netlist, analog P4 the measure margins at every corner, P5 DRC count and post-layout margins. `--baseline` writes the score, `--compare` fails on a lower composite, and wall time and cost stay informational. This is what an edit to a script, a prompt or a recipe is judged by before it lands.

CVDP gives the public number. `evals/cvdp/run.py` fetches the NVIDIA CVDP v1.1 dataset at a pinned commit, selects the problems whose harness needs only Icarus, cocotb and yosys, runs each problem's test commands natively under `eda` instead of in its container, and scores pass rate overall and by category. The result records the subset size and that this is not the official harness, and the ladder page shows the number beside the leaderboard figures the proposal quotes. A leaderboard submission needs the docker harness and is out of scope.

## 4. The optimise loop

The loop is Karpathy's autoresearch shape: an agent edits one file, a fixed-budget evaluator it cannot edit scores the result, the change stays only if the metric improved, otherwise git reverts it, every trial goes to a TSV, and it loops.

`optimise.py start --workspace <ws> --target rtl/uart.v --objective area|slack|power|score --trials N --wall <min>` freezes the evaluator: it copies the synth script, the liberty path, the SDC with the spec's clock, the visible tests, the formal properties, the holdout hash and the port list into `optimise/evaluator/`, hashes the directory and records the hash in `state.optimise`. Per trial: the optimiser agent gets the target file, the tail of `optimise/trials.tsv`, the last evaluator output and the rule of one change per trial; `optimise.py trial` diffs the tree and reverts any file other than the target, re-hashes the evaluator and aborts on a mismatch, runs the constraints (lint, sim, formal in a bounded fast profile), and if they pass runs the metric: yosys synth with the fixed script for cell area, OpenSTA on that netlist with the fixed SDC for worst slack, and OpenSTA power with switching activity from a VCD the fixed testbench produced. If the score improved the trial is committed; otherwise `git checkout -- <target>`. The TSV row is trial, timestamp, target sha, each constraint's result, area, slack, power, score, kept, and the agent's one-line note. The loop stops at N trials, the wall budget, or K non-improving trials in a row. On the winner the full gates run: holdout, formal without the bound, mutate. A winner that fails them is discarded and the last passing trial restored, which is what stops a change that deletes logic the visible tests never exercised.

What stops metric gaming. Correctness is a constraint, never a weighted term. Area comes from the fixed synth script on mapped cells, not from anything the agent runs. The clock period and the port list are locked, so a design cannot get faster by moving the goalposts or smaller by dropping a port. Activity for power comes from the fixed testbench. `must_keep` cells are checked after synth, so a ring oscillator cannot be optimised into a wire. The evaluator is hashed at start and at every trial. The write set is one file, enforced by the diff.

Analog sizing runs the same loop with `--target sizing/sizing.yaml` (widths, lengths, resistor and capacitor values, bias currents, each with bounds the designer set) and an evaluator that is the spec bench at typical with the score the spec declares (power at the spec-meeting point, or the smallest margin across measures). Every K-th kept trial and the winner run the full corner set. Two searchers write the same TSV: the agent, for changes a sweep cannot make, and `optimise.py numeric`, scipy differential evolution over the same file with the same evaluator, which does most of the work once the topology is right.

## 5. The analog flow, and the mixed-signal glue

`/ade` is `/hwde`'s method with the board replaced by a netlist and layout code. The analog-designer picks a topology from `skills/ade/reference/topologies/`, a library of parametrised SPICE templates with their design equations and sizing bounds written down (current mirror, differential pair, two-stage OTA, StrongARM comparator, bandgap, R2R ladder, current-starved ring), and writes the netlist against gf180mcu_fd_pr models with `sizing/sizing.yaml`. The bench-writer, in fresh context, writes `tb/*.cir` with `.measure` lines and a `.bounds.json` sidecar per bench, `/hwde`'s sim gate shape exactly. `corners.py` expands the spec's corner set from `corners.yaml` (process typical, ss, ff, sf, fs; temperature -40, 27 and 125; supply plus and minus 10 percent), and the default is the four extremes with typical, never fewer. Sizing is section 4's loop.

Layout is code. The layout-writer writes `layout/gen_<block>.py` against gLayout's GF180 mapped PDK on gdsfactory, placing the PDK's primitive cells (transistors with fingers and guard rings, MIM capacitors, resistors) and routing them explicitly, because there is no analog autorouter. `layout_gen.py` runs it and writes the GDS and an abstract for macro use. Magic, KLayout and netgen only judge the result: a DRC finding is clustered by layer and cell and becomes a work order for a layout-fixer, who edits the generator and never the GDS; an LVS mismatch carries netgen's unmatched nets and devices. Parasitics come from magic's extractor first and klayout_pex second. gLayout is not in the image and its upstream has been quiet for a year, so M9 installs a pinned fork into a venv made with `eda python` and proves it on an inverter and a mirror before anything else is built on it.

`/msde` adds three things. The splitter writes `interface.yaml`, every crossing signal with its direction, level, domain and load, and two specs carrying the same entries, and `split` checks they agree. The two sides run as nested workspaces with their own `state.json`, each driven through its skill's router. The integrator hardens the digital side with the analog GDS as a LibreLane macro so the tile's GDS comes out of one harden, writes the top netlist with the analog block as a subcircuit, and writes the co-simulation bench: ngspice's `d_cosim` code model loads the digital side compiled by iverilog through the image's `ivlng` bridge, so one bench drives both. That bridge is thinly documented, so M10 proves it on a two-inverter case first, with cocotbext-ams and a lock-step driver of ngspice's shared library as the fallbacks, in that order.

## 6. Milestones

Each milestone is one PR of roughly one to three thousand lines, written for a builder who has this document and the repo. Done criteria are commands whose exit code a script can check. Where this document says a module ports, the source is `~/dev/ai-ee/.claude/skills/hwde`, tests included.

### M0. The launcher

Goal: `bin/eda` runs every tool the flow needs from the unpacked image, in a bwrap session, with the subprocesses those tools spawn.

Why: nothing else can be built without it, and it is the assumption most likely to be wrong.

Boundaries: section 1.2 is the design; no docker, no namespaces, no host changes, no `/foss` required. Python entry points run through `eda python`. The shim directory is generated, not committed. `check_env.py` follows the script contract.

Done when `eda check-env` exits 0 and its JSON lists yosys, sby, mcy, verilator, iverilog, vvp, ngspice, openroad, sta, magic, klayout, netgen, librelane, cocotb and the gf180mcuD PDK with versions; and `tests/test_eda.py` passes with these real runs: a cocotb test on Icarus; `sby` proving a two-flop property (yices and smtbmc by absolute path); `yosys` synthesising an inverter to the gf180 liberty (yosys-abc); `verilator --binary` on a counter (the host compiler); LibreLane hardening the image's inverter example on gf180mcuD (openroad, magic, klayout, netgen as children); `ngspice -b` on a gf180 inverter; magic DRC, klayout DRC and netgen LVS on the demo `inv.gds`; `eda python -c "import cocotb, librelane, klayout, gdsfactory"`.

### M1. The engine skeleton

Goal: the ported engine on a fixture workspace, with no real gates yet.

Why: every skill stands on state, router, gate runner, stale map and fix loop, and porting them first keeps the domain work small.

Boundaries: section 1.3's port list and section 1.4's schema. `gates.yaml` registers every gate from section 1.5 with a stub tool that exits 2 "not built", so the registry validates now and later milestones replace stubs. `jobs.py` and `attest.py` included. `tasks.yaml` carries the shared verbs only. No skill directories.

Done when `eda python -m pytest tests/` passes with the ported `/hwde` tests that still apply plus new ones for jobs and the v3 schema; `task_router.py --validate --skill vde` is clean; `tests/fixtures/ws-empty` goes through `state.py init`, `edit --class rtl_edit`, `freshness` and `resume` with the marks section 1.6 lists; `jobs.py start` on a stub gate that sleeps records a pid, `status` reports done, and `state.gates` holds the result.

### M2. Simulation, lint, and planted faults

Goal: `spec_lint`, `lint`, `sim`, `holdout` and `mutate` as real gates, and the first two corpus rungs with faults.

Why: this is the floor of valid testing, and the counter and UART give every later milestone something real to run on.

Boundaries: `corpus/vde/counter8` and `corpus/vde/uart` with spec.md, spec.yaml, reference RTL, a reference tb with requirement tags, holdout tests, and a faults manifest naming the gate from section 1.5. `faults.py` implements corpus faults and the mcy-driven mutants; the PR reports `mutate`'s wall time per rung. The verbs `add-test` and `mutate` land in a first `skills/vde/reference/tasks.yaml`, no SKILL.md yet.

Done when `faults.py --skill vde` exits 0; `gate.py --gate mutate` on the counter reports a kill rate and survivors by class; a testbench that asserts nothing fails `mutate`; a requirement without a test fails `sim`; the pytest suite passes.

### M3. Formal, coverage, synthesis and the release record

Goal: `formal`, `cover`, `synth` and `release`, with `attest.py` producing `reports/checks.json`.

Why: formal is the answer to tests that pass too easily, and the record of what ran is the answer to silent failure.

Boundaries: properties in `formal/*.sv` with an `sby` config the engine generates from spec.yaml; two engines, bounded results recorded as bounded. `cover` under Verilator with the same cocotb tests. `synth` with the gf180 liberty. `release` strict, waivers ported from `/hwde` with durability bindings. Corpus faults for each gate, including the register-with-no-reset case that passes `sim` and fails `formal`.

Done when `faults.py --skill vde` exits 0 with the new gates; on the UART, `formal` proves every property and its result names each engine's verdict; `cover` fails a corpus variant with an unreachable state; `release` refuses a workspace whose RTL changed after `sim` passed and passes once the gates re-run; `reports/checks.json` validates against `engine/reference/checks.schema.json`.

### M4. Harden and signoff as jobs

Goal: `harden`, `timing`, `drc`, `lvs`, `glsim` and `precheck` on the Tiny Tapeout GF180 template, harden as a job.

Why: this is the half the course already does on GitHub's servers, brought onto the box where an agent reads the logs in minutes, plus the signoff the shuttle's precheck does not run.

Boundaries: `engine/reference/tt/` vendors the TT GF180 Verilog template's `config.json` and the precheck at pinned commits with their licence, and a script generates `tt_um_<name>.v` from `tt_pins`. Tile size and pin constraints come from the template, never typed by hand. `glsim` runs functional and SDF. GDS faults are planted with klayout's python module. The corpus adds `spi_fifo`.

Done when the counter, the UART and the SPI block each harden to a GDS inside the tile through `jobs.py`, and `timing`, `drc`, `lvs`, `glsim` and `precheck` pass on all three; `faults.py --skill vde` exits 0 with the six new faults; a job the test kills halfway is reported dead and finishes on restart; `release` passes on the counter with every digital gate fresh.

### M5. The /vde skill

Goal: `skills/vde` complete: SKILL.md, agents, recipes, remediations, templates, and the full run from a written spec to a released package with no hand edits.

Why: this is the proposal's first milestone, the part the course needs this term.

Boundaries: section 1.4's phases, testbench before RTL, fresh-context tb-writer, property-writer and reviewer, `/hwde`'s fix loop with `mutate` failures routed to the tb-writer, H1 and H2 in `/hwde`'s digest format, section 1.8's verbs. SKILL.md reaches the engine by the absolute path. The skill runs in a member workspace under bwrap, so the PR must be exercised there.

Done when a session running `/vde` on `corpus/vde/counter8/spec.md`, then the UART and the SPI block, reaches `release` green with no hand edits, transcripts and `state.json` attached; the corpus held-out tests pass on all three, run by `ladder.py` afterwards; a `review` of the course's own counter project returns a digest with the gate table; `task_router.py --validate --skill vde` is clean.

### M6. Evals

Goal: `evals/` with the ladder, per-stage benches on frozen fixtures, the CVDP runner and `ladder.md`.

Why: evals are one of the points of the project, and the optimise loop that follows needs baselines.

Boundaries: section 3. `ladder.py` scores a run a session made; it does not drive an agent. Fixtures are frozen with `bench.py --freeze` from the corpus reference rungs, because M5 kept no run workspaces, and are re-frozen from /vde runs once a /vde session takes a rung to release. CVDP at a pinned commit, the subset rule in `evals/cvdp/README.md`, the number reported with its caveat. The RISC-V rung joins the corpus with faults, and its row may stay red.

Done when `ladder.py --skill vde --rung uart --run <ws>` writes a result and `ladder.md` regenerates with all four vde rungs as reference baselines, its /vde column reading 'not run' until a skill run is scored; `bench.py --stage P4 --fixture uart_rtl --compare` exits 0 against its baseline and 1 when the fixture's RTL is broken; `evals/cvdp/run.py --limit 20` writes a result with pass rate by category; the full subset run is attached to the PR with its number.

### M7. The optimise loop for Verilog

Goal: `optimise.py` as section 4 describes, applied to PPA on the corpus.

Why: it is the flow's way of making working code good, and the frozen evaluator is what keeps it honest.

Boundaries: one target file, hashed evaluator, constraints are gates, fixed TSV, full gates on the winner. Numeric search is not in this milestone. The `optimise` verb and the optimiser agent land in `/vde`.

Done when `tests/test_optimise.py` shows an evaluator edit mid-loop aborts and reverts, an edit outside the target is reverted, a trial failing `sim` is logged as not kept, and a trial removing a `must_keep` cell is rejected; and a session's loop of at least ten trials on the UART is attached with its TSV showing a kept trial that reduced area with slack still non-negative and a winner passing holdout, formal and mutate.

### M8. The analog engine and the first rungs

Goal: `/ade`'s simulation half: spec, topology library, bench with bounds, corners, `netlist_lint`, `sim_tt`, `sim_pvt`, `bench_strength`, `mc`, and sizing through the optimise loop with numeric search.

Why: the proposal's second milestone; everything analog after this depends on the bench being trustworthy.

Boundaries: `sim_run.py` ported, driven through `eda ngspice -b`. `corners.yaml` and `corners.py`. Topologies: current mirror, differential pair, R2R ladder, StrongARM comparator, each template's header carrying its equations and bounds. `optimise.py numeric` added. Corpus `ade/mirror` and `ade/r2r_dac` with faults. `skills/ade/reference/tasks.yaml` with `spec`, `resize`, `add-corner`, `optimise`; no SKILL.md yet.

Done when `faults.py --skill ade` exits 0 on both rungs; `sim_pvt` on the mirror reports every measure at every default corner; `bench_strength` fails a bench whose bounds pass every mutant; `optimise.py numeric` on the DAC's sizing reaches every measure inside bounds from a deliberately wrong start, TSV attached; the pytest suite passes.

### M9. Analog layout as code, and the /ade skill

Goal: `layout_gen.py`, `drc`, `lvs`, `pex_sim`, analog `release`, the layout-writer and layout-fixer roles, and `skills/ade` complete with its full run.

Why: layout by generator code is the least proven part of the flow, and it needs its own sitting.

Boundaries: gLayout from a pinned fork into a venv made by `eda python -m venv`, recorded in `check_env`. Smoke first: an inverter and the mirror, DRC and LVS clean, before any skill work. DRC findings clustered by layer and cell. The corpus adds `ade/comparator` and `ade/bandgap`; the bandgap's row may stay red at layout.

Done when the mirror and the R2R DAC go from generator code to a GDS that passes `drc`, `lvs` and `pex_sim`; `faults.py --skill ade` exits 0 with the layout faults; a session running `/ade` on the mirror and the comparator reaches `release` green with no hand edits, transcripts attached; `ladder.md` gains the ade column.

### M10. The /msde skill

Goal: `split`, the nested runs, `cosim`, `top_drc`, `top_lvs`, `release`, and `skills/msde` complete.

Why: no published open flow covers digital and analog with LVS in the loop, and this is where the halves meet.

Boundaries: the `d_cosim` bridge proven on a two-inverter case before the gate is built, fallbacks in section 5's order and the choice recorded. The digital side hardens with the analog GDS as a LibreLane macro. Corpus `msde/sensor_counted` and `msde/ring_osc_div`; the DAC with an SPI register and the SAR ADC join as specs with faults and may stay red.

Done when `cosim` runs the ring oscillator with its divider in one bench and reports the divided frequency inside bounds; `faults.py --skill msde` exits 0; a session running `/msde` on `corpus/msde/sensor_counted/spec.md` reaches `release` with both nested workspaces released and `top_lvs` matching; `ladder.md` gains the msde column.

### M11. The course project

Goal: the course workspace picks one of its own term proposals and designs it as a subproject with its own repository, using the skills, to a package ready to submit.

Why: this is what the whole thing is for, and a real design with a deadline finds what a corpus cannot.

Boundaries: the choice between the proposals is the course's, recorded as a decision. The all-digital PLL is a `/vde` run with a `must_keep` ring; the charge-pump PLL is an `/msde` run. The subproject has its own repo with the TT template imported and cocotb 2.0.1 pinned as the course requires (the image has 2.1; `check_env` reports the difference). Nothing is submitted; the person signs off and pays.

Done when the subproject's `reports/checks.json` shows every applicable gate fresh and passed; the TT precheck passes on the repository as the shuttle's action would run it; `ladder.py` has a row for the project; and the subproject's `LEARNINGS.md`, compiled with `learnings.py`, has its general entries promoted into the skills.

## 7. Risks

- The launcher is the assumption everything rests on. A tool that insists on its container paths, or a child that cannot find its libraries, is found in M0 and not later. If some tool cannot run without `/foss`, the fallback is a read-only bind of the image's `foss` at `/foss` in the sandbox, a host change outside this repo.
- Analog layout by generator code, on gLayout that is unmaintained upstream and pinned against an older gdsfactory than the image ships. M9 starts with the smoke test for that reason; the fallback is generator code written directly against gdsfactory with the PDK's primitive cells.
- The cost of validity. `mutate` runs the suite once per mutant and `formal` can run long on a bigger block; M2 and M3 report wall times so `state.budgets` is set from measurement.
- The mixed-signal bridge is thinly documented; M10 proves it first.
- Tiny Tapeout's GF180 analog path is unproven on recent shuttles and the next shuttle is unconfirmed, which affects only which proposal M11 picks.
- Held-out tests inside a run are separated by discipline, not isolation; the corpus held-out tests, run afterwards, are the measurement to trust.
- The skill binds (three subdirectories of one repo, and the image directory) need support from the host that mounts skills, which does not exist today and is outside this repo.
