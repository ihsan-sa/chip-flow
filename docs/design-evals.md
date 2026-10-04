# Design evals: how chip-flow scores a chip design against measured truth

This is the research and the design for chip-flow's design evals. It answers four questions: how good evals are built, how much detail a design brief should give the model, whether the skills beat bare Claude Code on the same brief, and which chip quality signals can be measured and how. It then says how published open-silicon designs with known outcomes calibrate those signals.

The rule that runs through all of it is that a score is only as good as the ground truth it was checked against. Every grader here is deterministic code, and almost all of it already exists as a gate. A gate is trusted to the degree it has been measured against something real: planted faults it must catch (`faults.py`), held-out tests the run never saw, a proof, a simulation at every corner, or a chip that came back from the fab.

The design extends what exists, and adds no second harness and no second results store. The corpus ladder (`evals/ladder.py`) scores a finished run, the per-stage benches (`engine/scripts/bench.py`) score a stage on a frozen fixture, and the CVDP runner (`evals/cvdp/run.py`) gives the public number. All three write dated JSON under `evals/results/`. The new piece is `evals/scorecard.py`, which reads those results and turns them into area scores, a suite score with a confidence interval, and findings a skill can act on. It is the grader in sections 2 to 4.

## 1. How good evals are built

### Task design

The benchmarks that held up share one shape: each task is a real piece of work with a hidden, executable check.

- **VerilogEval** has 156 problems taken from HDLBits, an instructional Verilog site, and grades generated RTL by comparing its simulation outputs with a golden solution ("VerilogEval: Evaluating Large Language Models for Verilog Code Generation", ICCAD 2023, https://arxiv.org/abs/2309.07544). Its second version adds specification-to-RTL tasks, in-context examples and automatic failure classification, and finds that "prompt engineering remains crucial for achieving good pass rates and varies widely with model and task" ("Revisiting VerilogEval: A Year of Improvements in Large-Language Models for Hardware Code Generation", arXiv 2024, https://arxiv.org/abs/2408.11053). That finding is why brief detail is a measured variable here (section 2).
- **RTLLM** sets three goals in order: syntax, functionality, and design quality beyond correctness ("RTLLM: An Open-Source Benchmark for Design RTL Generation with Large Language Model", ASP-DAC 2024, https://arxiv.org/abs/2308.05345). RTLLM 2.0 grows it to 50 hand-crafted designs, each with a description, test cases and correct RTL ("OpenLLM-RTL: Open Dataset and Benchmark for LLM-Aided Design RTL Generation", ICCAD 2024, https://arxiv.org/abs/2503.15112). Its quality goal is the closest public precedent for scoring area and timing beside correctness.
- **CVDP** from NVIDIA has 783 problems in 13 categories covering RTL generation, verification, debugging, specification alignment and technical Q&A, in non-agentic and agentic forms, and reports no model above 34 % pass@1 on code generation ("Comprehensive Verilog Design Problems: A Next-Generation Benchmark Dataset for Evaluating Large Language Models and Agents on RTL Design and Verification", arXiv 2025, https://arxiv.org/abs/2506.14074). `evals/cvdp/` already runs 277 of the 302 non-agentic code-generation problems natively under `eda`.
- **RTL-Repo** gives the model a whole repository: 4,098 samples from 1,361 public GitHub repositories, with contexts up to 128K tokens ("RTL-Repo: A Benchmark for Evaluating LLMs on Large-Scale RTL Design Projects", arXiv 2024, https://arxiv.org/abs/2405.17378). It is the reminder that a block in context is harder than a block alone, which is what the msde rungs test.
- **ChipBench** exists because the older sets saturated. It reports that state-of-the-art Verilog generators exceed 95 % on existing benchmarks, and builds 44 hierarchical modules, 89 debugging cases and 132 reference-model tasks, on which the best model it tried reached 30.74 % on Verilog generation and 13.33 % on Python reference models ("ChipBench: A Next-Step Benchmark for Evaluating LLM Performance in AI-Aided Chip Design", arXiv 2026, https://arxiv.org/abs/2601.21448). Reference-model writing is a task chip-flow already gives its tb-writer.
- **FVEval** grades formal-verification work: writing SystemVerilog assertions from prose and from RTL, scored by formal equivalence against expert-written assertions ("FVEval: Understanding Language Model Capabilities in Formal Verification of Digital Hardware", arXiv 2024, https://arxiv.org/abs/2410.23299).
- **Analog** has fewer graded benchmarks. AnalogCoder grades generated circuits by simulation and reports 20 circuits designed, 5 more than GPT-4o alone ("AnalogCoder: Analog Circuit Design via Training-Free Code Generation", AAAI 2025, https://arxiv.org/abs/2405.14918). AnalogGym gives 30 topologies in five categories (sensing front ends, voltage references, LDOs, amplifiers, PLLs) with ngspice support for comparing sizing methods ("AnalogGym: An Open and Practical Testing Suite for Analog Circuit Synthesis", arXiv 2024, https://arxiv.org/abs/2409.08534). AMSNet and Masala-CHAI are datasets of schematics and SPICE netlists rather than graded design tasks ("AMSNet: Netlist Dataset for AMS Circuits", arXiv 2024, https://arxiv.org/abs/2405.09045; "Masala-CHAI: A Large-Scale SPICE Netlist Dataset for Analog Circuits by Harnessing AI", arXiv 2024, https://arxiv.org/abs/2411.14299). None of them grades a layout or a corner sweep, which `/ade` must.
- **The back end.** ChatEDA drives OpenROAD from an LLM agent through generated scripts ("ChatEDA: A Large Language Model Powered Autonomous Agent for EDA", IEEE TCAD 2024, https://arxiv.org/abs/2308.10204), and EDA Corpus is over 1,000 OpenROAD question-answer and script pairs ("EDA Corpus: A Large Language Model Dataset for Enhanced Interaction with OpenROAD", arXiv 2024, https://arxiv.org/abs/2405.06676). Both test tool use, not whether the chip that comes out is good.
- **Outside chips**, SWE-bench grades a patch by running the repository tests the human fix made pass ("SWE-bench: Can Language Models Resolve Real-World GitHub Issues?", ICLR 2024, https://arxiv.org/abs/2310.06770). SWE-bench Verified kept 500 of 1,699 screened tasks after 93 Python developers checked whether the issue was underspecified and whether the tests rejected valid fixes (https://openai.com/index/introducing-swe-bench-verified/). MLE-bench grades 75 Kaggle competitions against each one's human leaderboard ("MLE-bench: Evaluating Machine Learning Agents on Machine Learning Engineering", arXiv 2024, https://arxiv.org/abs/2410.07095).

The lesson chip-flow takes from SWE-bench Verified is that a task set needs an audit, and the grader must accept a correct answer before it is trusted to reject a wrong one. The corpus already does this: every rung carries a reference solution that must pass every gate, and `faults.py` plants each fault and checks the gate that must catch it. The lesson from MLE-bench is to anchor to human results, which is what section 5 is for.

### Testbench pass is not correctness

The one number that shapes this design comes from RealBench. Its authors ran GPT-4-Turbo on RTLLM 2.0 and found that 44.2 % of the code that passed the benchmark's own testbenches failed formal equivalence against the reference, and that testbenches in existing benchmarks reached as little as 59.1 % line coverage ("RealBench: Benchmarking Verilog Generation Models with Real-World IP Designs", arXiv 2025, https://arxiv.org/abs/2507.16200). This is the source of the "44 percent" in `docs/design.md` section 2.

So a simulation pass is a weak signal on its own. chip-flow answers it in three ways that already exist: held-out tests the run never saw, formal properties with a k-induction proof that is reported apart from a bounded check, and mutation testing that scores the testbench itself. The evals score all three separately rather than folding them into one pass bit.

### Held-out sets and contamination

A score on a task the system has seen measures memory, not skill. SWE-bench+ screened one agent's SWE-bench solves and found that 32.67 % had the answer in the issue text or its comments and 31.08 % passed only because the tests were weak; filtering them cut its resolve rate from 12.47 % to 3.97 % ("SWE-Bench+: Enhanced Coding Benchmark for LLMs", arXiv 2024, https://arxiv.org/abs/2410.06992). LiveCodeBench dates each problem and scores a model only on problems after its training cutoff ("LiveCodeBench: Holistic and Contamination Free Evaluation of Large Language Models for Code", arXiv 2024, https://arxiv.org/abs/2403.07974). VerilogEval's problems come from a public teaching site, so they are likely in training data.

chip-flow's risks are concrete. The corpus reference RTL, `holdout/` and `faults/` sit in this repo, and a session can read the repo. Today the held-out tests are kept out of the run by discipline and run afterwards by `ladder.py`, which is the strong form for the tests but not for the reference RTL. The eval runs must therefore start in a workspace that cannot read `corpus/`, and `ladder.py` should flag a run whose RTL is near-identical to the corpus reference, as MLE-bench runs a plagiarism check. If this repo goes public the corpus becomes trainable, so a held-out rung that has never been committed is the only clean one, and rungs should rotate in from it.

### Who grades

| grader | trusted for | not trusted for |
|---|---|---|
| code (the gates) | anything with a computable answer: test pass, proof, kill rate, coverage, area, slack, DRC and LVS counts, measure margins | judgement with no computable answer |
| a model | drafting a triage note, summarising a run | a score, because a judge shares the designer's blind spots |
| a person | ground truth on a sample: is this survivor equivalent, did this chip work | scale and repeatability |

Anthropic's guide to agent evals names the same three grader types, recommends starting with 20 to 50 tasks drawn from real failures, and says each trial should start from a clean environment so shared state does not cause correlated failures ("Demystifying evals for AI agents", Anthropic Engineering, 2026, https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). Every score here comes from code. People measure the code: the owner's mutant rulings say which survivors were equivalent, and silicon results say which signals predict a working chip.

### Variance and confidence intervals

Two kinds of noise matter.

- **Scoring noise** is close to zero. A bench on a frozen fixture with a pinned tool image gives the same score every run, and mutation uses a fixed seed. An interval on frozen inputs reflects which designs were sampled, not measurement noise.
- **Generation noise** is large. An agent run is not seedable, so "seed" here means a repeat number. The guidance is to repeat each task, report the mean with a standard error, cluster the error by task, and compare two systems by paired differences on the same tasks ("Adding Error Bars to Evals: A Statistical Approach to Language Model Evaluations", arXiv 2024, https://arxiv.org/abs/2411.00640).

`scorecard.py` reports each suite score with a seeded 95 % bootstrap interval over designs, and the share of rungs that count with a Wilson interval. Today each rung has one newest run, so it resamples rungs; once seeds repeat a rung, it should resample rungs and then repeats within each rung (a cluster bootstrap). With four rungs per skill the interval is wide, and the report says so instead of hiding it.

### pass@k, pass^k and mean score

pass@k is the chance that at least one of k tries succeeds, with the unbiased estimator from the Codex paper ("Evaluating Large Language Models Trained on Code", arXiv 2021, https://arxiv.org/abs/2107.03374). It fits a setting where a verifier picks the winner. pass^k, the chance that all k tries succeed, measures reliability ("τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains", arXiv 2024, https://arxiv.org/abs/2406.12045). A designer tapes out one block, so the headline here is the mean score per rung and the rate at which a rung counts. pass^k on "rung counts" is the reliability figure the owner cares about, because a skill that counts one run in three is not one they can hand a block to. pass@k is reported as a secondary number, since the gates do act as a verifier.

### Cost per run

Every ladder result records tokens, cost and wall time beside its score, and they never enter a composite. MLE-bench reports compute beside capability for the same reason: a score bought with ten times the spend is a different result. The recording is incomplete today. Of the 22 ladder results under `evals/results/ladder/`, 3 carry a session cost, because `--session-cost-usd` is optional, so the eval runs must pass it every time.

### Saturation

A benchmark stops separating systems when everyone scores near the top, which is ChipBench's whole motivation, and the Anthropic guide notes that a saturated eval still tracks regressions but no longer shows improvement. For chip-flow, counter8 will saturate first. A rung that every arm passes at full marks on every repeat moves to a regression set, stays as a guard, and leaves the headline. Scores are reported per suite version and never compared across versions.

## 2. Brief detail as a measured variable

This section and the next are the design for the full suite and none of it is built yet: no brief variants, no `ladder.py --detail` field and no bare-arm scoring workspace exist in the repo. How much the brief says is a factor in the experiment, not a choice made once. VerilogEval v2's finding that prompt shape moves the score is the reason.

**Three detail levels per rung.**

| level | file | what it carries |
|---|---|---|
| terse | `spec.terse.md` | what the block does, its interface table, and every requirement, in as few lines as that takes |
| typical | `spec.md` | today's brief: the requirements in prose with the behaviour described |
| full | `spec.full.md` | typical plus guidance: an architecture sketch, interface timing detail (cycle-by-cycle waveforms, reset values, what happens on a back-to-back start), and test hints (the corner cases a good testbench covers) |

**Every level states the same requirements and the same checked numbers.** For a vde rung those are the ports and widths, the clock period, and each requirement in `spec.yaml` with its id. For an ade rung they are the supply, the measures and their bounds. `spec.yaml`, `holdout/`, `formal/` and `faults/` are shared by all three levels, so the same graders apply and a variant cannot drift from its checks. A test in `tests/` checks both halves: every number and requirement id in `spec.md` appears in each variant, and no held-out test name or property name appears in any. What changes across levels is guidance only, so the experiment does not measure mind-reading.

An underspecified fourth level, which leaves a requirement out, would measure whether the model asks or picks a sane default. It is a later experiment because it needs an "asked a question" grader.

**How detail is held as a factor.** The session under test gets one level copied into a fresh workspace as `spec.md`, so the skill sees the same file name at every level. `ladder.py` records the level as a field (`--detail terse|typical|full`, default `typical`) in the same dated result under `evals/results/ladder/`. Same rung, same `spec.yaml`, same chip-flow commit, same model, same tool image. The design is levels by repeats, paired within rung, and `scorecard.py` reports the paired differences (typical minus terse, full minus typical) per rung and pooled, each with a cluster-bootstrap interval.

**What it would show.** Whether guidance moves the finished design or only the cost of getting there. The expectation worth testing is that interface timing detail moves function (held-out pass and the rate a rung counts), test hints move verification (kill rate and coverage), and architecture guidance moves implementation (area and slack) and fix attempts. If full detail buys the same design for less spend, that is a result too, so cost and fix attempts are reported per level. If terse briefs score as well as typical ones on the skill arm but not on the bare arm, the skill is supplying what the brief leaves out, which is the strongest argument for the skill.

The pilot levels go on counter8 and uart first, because their runs are cheap and both already have skill runs that counted.

## 3. Bare Claude Code against /vde, /ade and /msde

The question is whether the skills produce better chips than Claude Code with the same tools and no skill.

**Arms.** Both arms get the same `spec.md` at the same detail level, the same tool image through `bin/eda`, the same model, and a fresh workspace that cannot read `corpus/`. The skill arm runs `/vde`, `/ade` or `/msde` `full-run`. The bare arm gets no skill, no engine scripts and no agent definitions, only `eda` on PATH and the brief. Both briefs end with the same short footer saying where deliverables go (`rtl/`, `tb/`, `netlist/`, `layout/`, a GDS for a hardened block), so the scorer can find them; the footer is identical in both arms and is not guidance.

**Grading.** Both arms are scored after the run ends by the same graders. A bare workspace has no `state.json` and no recorded gate passes, and the skill arm's recorded passes are its own grading, so neither is trusted for the comparison. `ladder.py` builds a scoring workspace from the corpus rung (`spec.yaml`, the corpus `formal/` properties, `holdout/`), copies in the arm's deliverables, and runs every gate the skill owes through `gate.py`. Function is then judged by tests and properties the arm never saw, which is the strong form of `docs/design.md` section 2. The arm's own testbench is scored too, by `mutate` and `cover`, because writing a testbench that kills mutants is part of the job. A bare run that writes no testbench scores zero on verification and is reported as "no testbench", and one that produces no GDS scores zero on implementation and is reported as "not hardened". A missing artefact is a result.

Analog and mixed-signal have no `holdout` gate today (`ladder.py` records `n/a` for them). The analog equivalent is to re-run `sim_pvt` and `mc` on the arm's netlist with the corpus's own `tb/` and bounds rather than the arm's, so a bench the arm weakened cannot pass it. That swap is the extension `/ade` and `/msde` rungs need before their bare-against-skill numbers mean anything, and until it lands the scorecard leaves their function area unmeasured rather than grading the arm's own bench.

**Seeds.** A repeat number, not a seed. Each result records the arm, the detail level, the repeat, the chip-flow commit, the model id and the tool image digest. Three repeats per cell is the starting point, because it is the smallest number that gives pass^3 and a spread.

**What counts.** A rung counts on the existing rule: every gate green, held-out tests pass, no hand edits. Owner rulings stay in their own column for both arms, and the bare arm can receive one the same way. Once ladder results carry the arm, `scorecard.py` should report, per skill and pooled, the paired difference in the rate a rung counts and in each area score, each with a cluster-bootstrap interval, and the findings list for each arm shows where it fails. Cost and wall time are reported per arm beside the score: a skill that counts twice as often at three times the cost is a different finding from one that counts twice as often at the same cost.

## 4. Which chip quality signals are measurable

Three areas of quality, and process reported apart. Each signal is read from a gate's recorded result or a ladder field; `scorecard.py` reads them and does not re-run tools.

| signal | area | how it is measured | gate / script |
|---|---|---|---|
| gates green | function | every gate the skill owes has a fresh pass whose input hashes match, or a bound waiver | `attest.py`, `release` |
| simulation against a reference model | function | cocotb tests score the DUT against a Python model, transaction by transaction or cycle by cycle | `sim` (`check_sim.py`) |
| held-out tests | function | corpus `holdout/` run after the run ends, on a copy of the workspace | `ladder.py`, `holdout` (`check_holdout.py`) |
| formal properties | function | SymbiYosys, k-induction through smtbmc and abc pdr; proven and bounded-with-depth are reported apart, and bounded never counts as proven | `formal` (`check_formal.py`) |
| mutation kill rate | verification | mcy mutants of the RTL at a fixed seed against the visible tests and bounded formal; proven-equivalent survivors left out, ruled survivors counted apart | `mutate` (`check_mutate.py`) |
| line and toggle coverage | verification | the same cocotb tests under Verilator with coverage, against `spec.yaml` thresholds | `cover` (`check_cover.py`) |
| requirement coverage | verification | every requirement id touched by a tagged test | `sim`, `spec_lint` |
| area | implementation | yosys cell area on the flattened, liberty-mapped netlist | `synth` (`check_synth.py`) |
| worst slack | implementation | OpenSTA on the hardened netlist with SPEF, worst setup and hold slack across every corner the flow produced | `timing` (`check_timing.py`) |
| DRC | implementation | magic and klayout's gf180mcu deck, both must report zero | `drc` (`check_drc.py`) |
| LVS | implementation | netgen, extracted layout against the powered netlist | `lvs` (`check_lvs.py`) |
| gate-level simulation | implementation | the same cocotb suite on the hardened netlist, functional and timed | `glsim` (`check_glsim.py`) |
| precheck | implementation | the vendored Tiny Tapeout precheck on the tile | `precheck` (`check_precheck.py`) |
| measure margins at PVT | function (analog) | ngspice over the corner set (typical plus the four extremes at least); the worst margin to the nearest bound, per measure | `sim_tt`, `sim_pvt` (`check_sim_pvt.py`) |
| Monte Carlo yield | function (analog) | ngspice Monte Carlo when `spec.yaml` asks for it, yield against the spec | `mc` (`check_mc.py`) |
| bench strength | verification (analog) | device mutants (size doubled, connection removed, type flipped, bias halved), the share the bench kills | `bench_strength` (`check_bench_strength.py`) |
| analog DRC and LVS | implementation (analog) | on the GDS regenerated from the layout code | `drc`, `lvs` (`check_analog_drc.py`, `check_analog_lvs.py`) |
| post-layout margins | implementation (analog) | magic extraction with R and C, then the measures again | `pex_sim` (`check_pex_sim.py`) |
| co-simulation | function (msde) | one cocotb bench drives the Icarus digital side against the ngspice analog block | `cosim` (`check_cosim.py`) |
| top DRC, LVS, precheck | implementation (msde) | on the assembled top with the analog macro | `top_harden`, `top_drc`, `top_lvs`, `precheck` |
| stage benches | regression | a stage's gates on a frozen fixture, composite against a baseline | `bench.py --compare` |

**How a signal becomes a score.** Pass-or-fail gates score 1 or 0. Kill rate and coverage score as fractions, and a missed threshold shows as a finding. Area and slack have no absolute target in most specs, so they are scored against the rung's reference baseline (the corpus reference run under "Reference baselines" on `ladder.md`): area as reference area over run area, capped at 1, and negative slack as a failing timing gate. Analog margins would be scored as the worst margin over corners as a share of the bound's span, once `ladder.py` records them. `scorecard.py` owns these formulas, and the area weights stay provisional until silicon results can fit them (section 5).

**What the pilot scores today.** `scorecard.py` reads only what `ladder.py` records, which is coarser than the table: the gates green or not, the held-out status, kill rate, line coverage, area and worst slack. So the pilot scores four areas, each in [0, 1]: signoff (the share of the gates the run owes that are green, which folds the per-gate rows above into one number), function (the corpus held-out tests), verification (kill rate and line coverage) and implementation (slack at or above zero, and area against the reference). An area the skill owes but the run never measured scores 0, because a gate that did not run is a refusal. /vde owes all four. /ade and /msde owe signoff and function only, and their function stays unmeasured until the analog held-out swap in section 3 lands, so their composite today is signoff alone. That is the largest gap the pilot shows: the analog quality signals in the table exist as gates but are not yet in the ladder record.

**Findings a skill can act on.** Every finding has the shape `{gate, severity, what, fix, route_to}`: the gate that found it, how bad it is, one sentence on what is wrong, one sentence on what to change, and which agent should change it. `route_to` follows the routing the skills already use, so a mutate survivor goes to the tb-writer, a formal counterexample to the property-writer first, and a timing violation to the fixer (the role `fix_dispatch.py` gives the harden domain). A skill can read the list mid-design and act on the top items. Held-out results, the reference baselines and silicon outcomes never enter that list, because a loop that sees its test learns the test; a held-out failure reaches a run only as the requirement id, as it does today.

**Process signals, reported apart.** Hand edits, owner rulings, fix attempts, tokens, cost and wall time measure the path to the design, not the design. They are reported beside the score and never mixed into it, for three reasons. A cheap broken design must not outscore an expensive working one. A ruling is a person answering a question through a documented channel, not a defect in the chip, which is why `ladder.py` already counts rulings apart from hand edits and scores an undeclared ruling as a hand edit. And the bare and skill arms spend differently by design, so mixing cost in would turn a quality comparison into a price comparison.

## 5. Human-designed corpus and silicon ground truth

The gates say whether a design meets its checks. Only silicon says whether those checks predict a working chip. The plan is to run the same gates on published open-silicon designs whose silicon outcome is known, and see which signals separate the designs that worked from the ones that did not.

### What exists

- **Tiny Tapeout on GF180.** The run list shows TTGF0p2, a 52-design test shuttle on wafer.space shuttle WS-2512 with chips expected 2026-05-21, and TTGF26a and TTGF26b, 95 and 90 designs on WS-2606 with chips expected 2026-11-07 (https://tinytapeout.com/runs). These are the best match for chip-flow, because `/vde` hardens into the same Tiny Tapeout GF180 tile and `precheck` runs the same vendored precheck, so the gates run on their GDS without adaptation. Neither the run list nor the TTGF0p2 page (https://tinytapeout.com/chips/ttgf0p2/) publishes a per-design silicon status.
- **wafer.space.** Run 1 wafers were received and dies sorted and shipped (https://www.crowdsupply.com/wafer-space/gf180mcu-run-1), and Tiny Tapeout reported its first wafer.space tapeout functional on 2026-04-30 (https://tinytapeout.com/news/wafer-space-gf180/). I found no per-design result list for Run 1.
- **Efabless and Google GFMPW-0 and GFMPW-1.** The shuttle project lists were on the Efabless platform (https://platform.efabless.com/projects/shuttle/15 and /21), and design repositories are mirrored per slot (for example https://foss-eda-tools.googlesource.com/third_party/shuttle/gf180mcu/mpw-000/slot-018). Efabless shut down in 2025 (https://tinytapeout.com/news/efabless-shutsdown/), so those lists may not stay reachable. Outcomes are scattered across project repositories: one GFMPW-0 design's bring-up repository documents every silicon bug with its severity and workaround (https://github.com/AvalonSemiconductors/AS2650-bring-up). I found no shuttle-wide record of which GFMPW designs worked.
- **Papers with measured GF180 silicon.** An OQPSK modulator built with open tools and the open GF180MCU PDK in the Caravel harness, with measurements of the chip ("Design and Test of Offset Quadrature Phase-Shift Keying Modulator with GF180MCU Open Source Process Design Kit", Electronics, 2024, https://doi.org/10.3390/electronics13091705). An analog front end with op-amps and a 14-bit SAR ADC on a GF180MCU shuttle, used to read flexible sensors, reported through wafer.space and an IEEE Solid-State Circuits Magazine article (https://www.crowdsupply.com/wafer-space/gf180mcu-run-1/updates/openfasoc). These are the analog and mixed-signal anchors, few as they are.

So the designs are public, but the outcomes are not collected anywhere. Building the label set is the first job: for each design, a status of works, partly, dead or unknown, with its source, and a separate flag when the failure was outside the block (the shuttle harness, packaging, the test setup). A shuttle-level failure says nothing about the design and is left out.

### How a human design is scored

The same gates run on the human design, through `gate.py`, in a scoring workspace built the way section 3 builds one for the bare arm.

- Digital. A Tiny Tapeout project ships its RTL, an `info.yaml` with pinout and clock, its GDS, and usually a cocotb test. A `spec.yaml` is written from `info.yaml` and the project's documentation (ports, clock period, requirements as the documentation states them). Then `lint`, `synth`, `timing`, `drc`, `lvs`, `glsim` and `precheck` run on their artefacts, and `mutate` and `cover` run their own testbench. Their kill rate is the most interesting number, because it asks whether weak tests preceded silicon bugs. `formal` and held-out tests need properties and a reference model nobody wrote for these designs, so they are left out except on a few designs where writing them is worth it.
- Analog. Where the netlist and layout are published, `drc`, `lvs` and `pex_sim` run on them, and `sim_pvt` runs the corners against bounds taken from the design's own stated targets. Analog designs with both a published netlist and a measured result are rare, and I expect this side to stay small.
- Mixed-signal. `top_drc`, `top_lvs` and `precheck` run on the assembled top. `cosim` needs a bench nobody wrote, so it is left out.

### How silicon calibrates the signals

For each signal, a calibration pass of `scorecard.py` would compare its distribution in designs that worked against designs that did not, as an AUC with a bootstrap interval, and report which signals separate the two. Three cautions shape how that is read. Every taped-out design already passed the shuttle's precheck, so DRC, LVS and precheck will be near-saturated in this set and cannot show much; the variance is in function and verification. The label set will be small, so the result is descriptive and the intervals wide. And designers who publish bring-up notes are not a random sample. The calibration is still worth having, because a signal that does not separate working from dead silicon should not carry weight in a composite, and a signal that does should carry more. The area weights stay provisional until this has enough designs to fit them.

The human corpus also anchors the scores the way MLE-bench's human leaderboard does. A design that worked in silicon should score high, and a gate that flags many problems on designs known to work is miscalibrated, which is a finding about the gate rather than the design.

## 6. Against ai-ee's design-evals

ai-ee is building the same kind of evals for PCBs. Its design document is not landed yet, so this compares against its current draft and may need updating when it lands.

Where this follows it:

- **Measured truth, not opinion.** Every score is code checked against something real, and people measure the code instead of scoring designs.
- **Findings with a fix line.** The same idea of a ranked list a model reads mid-design, each item saying what to change, with the held-out checks and the human corpus kept out of the feed.
- **Intervals by cluster bootstrap.** Resample tasks, then repeats, and report paired differences between arms and levels.
- **Detail levels.** Terse, typical and full, sharing the same requirements and checks, with guidance the only thing that varies.
- **Bare against the skill.** The same brief to both arms in the same environment, scored by the same graders after the run.
- **A human corpus.** Human designs through the same graders, with real-world outcomes to calibrate against.

Where it departs, and why:

- **Chip ground truth is cheaper.** A PCB check is often a heuristic whose precision has to be measured from triaged findings, and the board's truth is a bench bring-up. Here most ground truth is simulation against a reference model, a formal proof, STA, DRC and LVS, all exact and repeatable. Silicon is the expensive truth and is used only to calibrate.
- **The graders are the existing gates.** ai-ee adds new checks for its evals. Here the gates the skills already run are the graders, each already tested by planted faults, and the evals add only the scoring and the held-out runs on top. So there is no per-check precision weighting: a gate result is a pass or a measured number, not a heuristic finding.
- **Rulings are counted apart.** An owner ruling on a mutation survivor is a documented human answer with no PCB counterpart, and it is scored in its own column rather than as a hand edit or a defect.
- **Analog PVT.** Analog quality is margins across process, voltage and temperature corners and Monte Carlo yield, and post-layout margins after extraction. That axis has no direct PCB counterpart in ai-ee's draft.
- **Contamination is sharper.** The corpus reference RTL and held-out tests sit in a repo that may go public, so held-out rungs must rotate in from outside it.

## 7. Pilot: the existing runs scored
<!-- scorecard:begin -->
Generated by `evals/scorecard.py --doc` from the newest ladder and bench results; do not edit by hand.

| skill | designs scored | composite | 95% CI | rungs counted | 95% CI |
|---|---|---|---|---|---|
| vde | 3 | 1.00 | [1.00, 1.00] | 3/3 | [0.44, 1.00] |
| ade | 2 | 0.89 | [0.89, 0.89] | 0/2 | [0.00, 0.66] |
| msde | 2 | 0.57 | [0.14, 1.00] | 0/2 | [0.00, 0.66] |
| all | 7 | 0.85 | [0.60, 0.98] | 3/7 | [0.16, 0.75] |

| design | kind | phase | counts | composite | signoff | function | verification | implementation | top finding |
|---|---|---|---|---|---|---|---|---|---|
| ade/mirror | skill | P5 | no | 0.89 | 0.89 | - | - | - | release: no recorded pass |
| ade/r2r_dac | skill | P5 | no | 0.89 | 0.89 | - | - | - | release: no recorded pass |
| msde/ring_osc_div | skill | P2 | no | 0.14 | 0.14 | - | - | - | cosim: no recorded result |
| msde/sensor_counted | skill | P4 | no | 1.00 | 1.00 | - | - | - | 2 hand edit(s) |
| vde/counter8 | skill | P8 | yes | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | - |
| vde/spi_fifo | skill | P8 | yes | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | - |
| vde/uart | skill | P8 | yes | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | - |
| vde/counter8 | reference | P0 | yes | 0.99 | 1.00 | 1.00 | 0.98 | 1.00 | kill rate 0.96 |
| vde/riscv | reference | P0 | no | 0.47 | 0.40 | 1.00 | 0.50 | 0.00 | drc: no recorded result |
| vde/spi_fifo | reference | P0 | no | 0.67 | 0.33 | 1.00 | 0.85 | 0.50 | cover: last recorded result is FAIL |
| vde/uart | reference | P0 | no | 0.71 | 0.47 | 1.00 | 0.87 | 0.50 | drc: no recorded result |

| stage bench | composite | gates not passing |
|---|---|---|
| P3 counter8_tb | 1.00 | - |
| P4 counter8_rtl | 1.00 | - |
| P4 uart_rtl | 0.81 | mutate, formal |
<!-- scorecard:end -->

Run it with `eda python evals/scorecard.py --record --doc docs/design-evals.md`; it runs no design and no gate, and `--record` keeps each scorecard as a dated JSON under `evals/results/scorecard/`. Three things stand out. The three /vde rungs that reached release all score 1.0, so on today's record the vde score is saturated: three runs cannot separate a good skill from a lucky one, and the finer signals in section 4 (slack margin, formal depth, toggle coverage) are what would spread them. The /ade and /msde composites are signoff alone, because the ladder records no analog margin and no held-out result for them, so 0.89 for the two /ade runs says that most gates they owe were green, not that the circuits meet spec. And the reference rows are low because their signoff gates were never run on the reference RTL, which the scorecard counts as refusals; that is a gap in the reference baselines, not in the reference designs.


## 8. Cost of the full suite
<!-- cost:begin -->
Generated by `evals/scorecard.py --doc`; do not edit by hand.

The full suite is 12 rungs x 3 detail levels x 2 arms x 3 seeds = **216 design runs**.

Of 17 skill runs on record, 3 carry a cost: $39 to $70 a run, median $67. At those figures the suite costs $8,424 to $15,012, median $14,472. One rung per skill instead of every rung is 54 runs, about $3,618 at the median.
The median recorded wall time is 7.6 h a run, which includes waits on owner rulings and blocked fixes.
<!-- cost:end -->

The per-run figure is thin. Only three runs recorded a cost, all of them /msde on one rung, so the estimate assumes every skill and both arms cost what /msde did; I expect the bare arm to be cheaper per run, because it runs fewer gates, but nothing on record measures that. Recording `--session-cost-usd` on every scored run is the first thing the full suite needs. The suite is not run here. The smaller first step, one rung a skill at every detail level and both arms, answers the bare-against-skill question for a quarter of the cost, and the planning seat takes the choice to the owner.


## What I did not verify

- I found no published per-design silicon status for any GF180 shuttle: not for TTGF0p2, wafer.space Run 1, GFMPW-0 or GFMPW-1. Whether TTGF0p2 chips arrived on their expected date, and whether TTGF26a and TTGF26b will, I did not check beyond the run list. Whether GFMPW-1 silicon was delivered after Efabless closed is unknown to me.
- I did not confirm that the GFMPW-0 bring-up repository ships its RTL, nor read the IEEE Solid-State Circuits Magazine article behind the analog front end; that article's title and authors are not verified here.
- The OQPSK paper's shuttle name and the full measured results were not read, only its abstract and record.
- Venues are given where a source page or record stated them (VerilogEval ICCAD 2023, RTLLM ASP-DAC 2024, OpenLLM-RTL ICCAD 2024, AnalogCoder AAAI 2025, ChatEDA IEEE TCAD 2024, SWE-bench ICLR 2024); otherwise the arXiv year is given. I did not check later publication venues for the arXiv-only entries.
- Citations were checked for title, year, link and the specific numbers quoted, against the abstract or the cited passage, not re-read in full.
- The detail-level split, the scoring workspace for the bare arm, the analog held-out swap, the copy check against the corpus reference and the `--detail` field are proposals in this document. None of them is built yet.
- The scoring formulas in section 4 describe what `scorecard.py` is meant to do; the code is the authority where they differ.
