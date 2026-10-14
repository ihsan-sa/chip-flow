# chip-flow goals

Claude Code skills for designing chips with open tools: `/vde` for digital blocks in Verilog, `/ade` for analog blocks, and `/msde` for a mixed-signal task that drives the other two. Every result has to pass gates that are hard to fool: lint, simulation, formal proof, hardening, timing, DRC, LVS, SPICE at every corner, and planted faults that each gate must catch.

## Proposed goals

Filed by runs. Each says what done looks like as something a run can check, and keeps its status (open, done, failed, dropped) with the pull requests that moved it. At most three are open at once.

### Finish the round-based eval ladder for counter8

- **Status:** open
- **What:** The branch `track/evals-counter8-round` holds the first round that compares a bare Claude Code run with one using the chip-flow skill. It's built for the counter8 rung and isn't merged yet. `evals/round/round.py` runs 18 headless design runs, one at a time: two arms (bare and skill), three brief levels (`corpus/vde/counter8/spec.terse.md`, `spec.md`, `spec.full.md`) and three repeats. A second invocation resumes where the first stopped. Each run happens in a bubblewrap sandbox (`evals/round/sandbox.py`) that can't read `corpus/`, behind a proxy that lets through only allowed hosts (`proxy.py`). An isolation probe (`probe.py`) has to pass in that exact sandbox before each run starts. The spend limits are in code: $500 across every round, booked in one ledger, and $70 a run. A usage limit in force, or a change to the agent's own config directory during a run, stops the round. It never waits a usage limit out, but it does pause while the machine's load is high. `evals/ladder.py --deliverables` scores both arms through the same code from the files that `evals/arms/footer.md` asks for, and `evals/scorecard.py --round` reports the paired differences. The branch is nine checkpoint commits on top of #56. The last of them were a review fix pass, which added the shared ledger and lock and the live watch on the config directory, so the whole branch needs a fresh review before it lands. Rebase it onto main, review it, fix what the review finds, and open a PR. Running the 18 paid runs isn't part of this goal.
- **Done when:** a PR with this work is merged into main, and on that PR `tests/test_eval_round.py`, `tests/test_evals_ladder.py`, `tests/test_evals_scorecard.py`, `tests/test_counter8_briefs.py` and `tests/check.sh` all pass. `docs/design-evals.md` must also say which parts of the round are built and which sandbox gaps are still open.

## Later

Goals waiting for an open slot. When a proposed goal closes, the run that closed it moves the top one here up into Proposed goals as open.

## Closed

What has landed, from the git history (a line per PR).

- Land the analog LVS number check: **Status:** done. `check_analog_lvs` reads exponent strings and refuses expressions ([#73](https://github.com/ihsan-sa/chip-flow/pull/73)). Its two new tests aren't marked slow, so `tests/check.sh` runs them, and it passed on main at 1531ab9.
- Fill the analog macro so top DRC and precheck pass DCF.1a ([#70](https://github.com/ihsan-sa/chip-flow/pull/70))
- top_lvs blackbox guard counts every device, not just FETs ([#69](https://github.com/ihsan-sa/chip-flow/pull/69))
- Harden places a macro at its LEF origin, not its GDS origin ([#68](https://github.com/ihsan-sa/chip-flow/pull/68))
- Let a block's spec set the ngspice timeout for sim checks ([#67](https://github.com/ihsan-sa/chip-flow/pull/67))
- Macro LEF exports only label-sized pins, not whole nets ([#66](https://github.com/ihsan-sa/chip-flow/pull/66))
- pex_sim reorders pins across continuation lines ([#65](https://github.com/ihsan-sa/chip-flow/pull/65))
- Fast CI job is named check ([#64](https://github.com/ihsan-sa/chip-flow/pull/64))
- Let a grid name extra corners; score passive-corner bounds ([#63](https://github.com/ihsan-sa/chip-flow/pull/63))
- sim_tt and sim_pvt score bounds scoped to tt_27c ([#62](https://github.com/ihsan-sa/chip-flow/pull/62))
- bench_strength scores bounds scoped to tt_27c ([#61](https://github.com/ihsan-sa/chip-flow/pull/61))
- Speed up bench_strength: skip killed mutants, run benches in parallel ([#60](https://github.com/ihsan-sa/chip-flow/pull/60))
- Gate freshness hashes inputs at start, not at finish ([#59](https://github.com/ihsan-sa/chip-flow/pull/59))
- Let bench_strength kill on absolute moves of near-zero measures ([#58](https://github.com/ihsan-sa/chip-flow/pull/58))
- Fix glsim's SDF pass for escaped instance names in INTERCONNECT ([#57](https://github.com/ihsan-sa/chip-flow/pull/57))
- Empirical evals that score how good a chip design is ([#56](https://github.com/ihsan-sa/chip-flow/pull/56))
- Harden a block whose spec names more than one clock ([#55](https://github.com/ihsan-sa/chip-flow/pull/55))
- Route formal counterexamples to the property-writer first ([#54](https://github.com/ihsan-sa/chip-flow/pull/54))
- Cut only the feedback term of an async self-reset loop ([#53](https://github.com/ihsan-sa/chip-flow/pull/53))
- Synth gate stops flagging a design's own submodules ([#52](https://github.com/ihsan-sa/chip-flow/pull/52))
- Fix the check_mutate slow test that is red on main ([#51](https://github.com/ihsan-sa/chip-flow/pull/51))
- sim_pvt skips a bench at corners where nothing is scored ([#50](https://github.com/ihsan-sa/chip-flow/pull/50))
- Score sensor_counted: count rulings apart, bound formal cover ([#49](https://github.com/ihsan-sa/chip-flow/pull/49))
- Release spi_fifo: clear partial byte on cs_n, then P5-P8 ([#48](https://github.com/ihsan-sa/chip-flow/pull/48))
- Prove spi_fifo at P4 formal and score its vde rung ([#47](https://github.com/ihsan-sa/chip-flow/pull/47))
- README says what chip-flow has built and released ([#46](https://github.com/ihsan-sa/chip-flow/pull/46))
- check_pex_sim reports an unlabelled probe as a finding ([#45](https://github.com/ihsan-sa/chip-flow/pull/45))
- Take the ring_osc_div rung from analog layout to release ([#44](https://github.com/ihsan-sa/chip-flow/pull/44))
- Mutation sims outlive the test or run that started them ([#43](https://github.com/ihsan-sa/chip-flow/pull/43))
- check_formal proves designs with self-resetting async loops ([#42](https://github.com/ihsan-sa/chip-flow/pull/42))
- Finish scoring uart, mirror, r2r_dac and ring_osc_div ([#41](https://github.com/ihsan-sa/chip-flow/pull/41))
- Score the six small ladder rungs to release ([#40](https://github.com/ihsan-sa/chip-flow/pull/40))
- Fix sensor_counted failing mutate at the baseline ([#39](https://github.com/ihsan-sa/chip-flow/pull/39))
- Mutate reads the RTL without SYNTHESIS defined, so it mutates the model the sim gate tests ([#38](https://github.com/ihsan-sa/chip-flow/pull/38))
- Synth uses the Tiny Tapeout template's std-cell library; a held-out test that dies in its own stimulus goes to the tb-writer ([#37](https://github.com/ihsan-sa/chip-flow/pull/37))
- tests/check.sh smokes take a CPU budget, not a fixed wall timeout, so heavy load no longer fails them ([#36](https://github.com/ihsan-sa/chip-flow/pull/36))
- Formal turns on multiclock when a design needs it; the PVT sweep gains passive corners ([#35](https://github.com/ihsan-sa/chip-flow/pull/35))
- Engine-tests gate fails on the suite, not on machine load ([#34](https://github.com/ihsan-sa/chip-flow/pull/34))
- Gates report B sources, n/a mc and formal-only edits honestly ([#33](https://github.com/ihsan-sa/chip-flow/pull/33))
- The split gate refuses a pad pin among the crossing signals ([#32](https://github.com/ihsan-sa/chip-flow/pull/32))
- Formal stops calling a failed induction step a counterexample; analog sims take a relative workspace path ([#31](https://github.com/ihsan-sa/chip-flow/pull/31))
- Every block the skills build gets a readable design document ([#30](https://github.com/ihsan-sa/chip-flow/pull/30))
- M7: the optimise loop for Verilog ([#29](https://github.com/ihsan-sa/chip-flow/pull/29))
- Fix two dac_tile top_harden slow tests that failed on main ([#28](https://github.com/ihsan-sa/chip-flow/pull/28))
- This repo's checks run on GitHub Actions ([#27](https://github.com/ihsan-sa/chip-flow/pull/27))
- Run /msde to release on sensor_counted (M10 part 3) ([#26](https://github.com/ihsan-sa/chip-flow/pull/26))
- top_harden: PDN for an analog macro inside the TT tile ([#25](https://github.com/ihsan-sa/chip-flow/pull/25))
- Checkpoints need a live answer; nested blocks named by the msde split ([#24](https://github.com/ihsan-sa/chip-flow/pull/24))
- Analog layout: keep polygon holes, skip fill decks, LVS std cells and resistor W/L ([#23](https://github.com/ihsan-sa/chip-flow/pull/23))
- bench_strength: a baseline warning neither refuses nor kills ([#22](https://github.com/ihsan-sa/chip-flow/pull/22))
- top_harden: bind a width-N interface signal to its N macro pins ([#21](https://github.com/ihsan-sa/chip-flow/pull/21))
- Add the Tiny Tapeout GF180 analog tile to the top flow ([#20](https://github.com/ihsan-sa/chip-flow/pull/20))
- ade: fixed-VDD corner grid, DAC routing, longer step and sim windows ([#19](https://github.com/ihsan-sa/chip-flow/pull/19))
- netlist_lint: accept GF180 std cells, follow .subckt continuations ([#18](https://github.com/ihsan-sa/chip-flow/pull/18))
- check_formal: bind asserts silently dropped, DUT regs invisible ([#17](https://github.com/ihsan-sa/chip-flow/pull/17))
- Score /vde release runs and refreeze the benches from them ([#16](https://github.com/ihsan-sa/chip-flow/pull/16))
- M9 part 2: the /ade skill, with a real ade release gate ([#15](https://github.com/ihsan-sa/chip-flow/pull/15))
- M10 part 2: the /msde skill, top_harden and the top DRC and LVS gates ([#14](https://github.com/ihsan-sa/chip-flow/pull/14))
- design: record M10's split into three parts ([#13](https://github.com/ihsan-sa/chip-flow/pull/13))
- M6: score a run against its corpus rung and regenerate the ladder ([#12](https://github.com/ihsan-sa/chip-flow/pull/12))
- M4: harden and signoff as jobs ([#11](https://github.com/ihsan-sa/chip-flow/pull/11))
- M9 part 1: analog layout as code, drc/lvs/pex_sim gates ([#10](https://github.com/ihsan-sa/chip-flow/pull/10))
- M8: the analog engine and the first /ade rungs ([#9](https://github.com/ihsan-sa/chip-flow/pull/9))
- M10 part 1: split and cosim gates on cocotbext-ams ([#8](https://github.com/ihsan-sa/chip-flow/pull/8))
- M5: the /vde skill ([#7](https://github.com/ihsan-sa/chip-flow/pull/7))
- M3: formal, coverage, synthesis and the release record ([#6](https://github.com/ihsan-sa/chip-flow/pull/6))
- M2: simulation, lint and mutation gates with planted faults ([#5](https://github.com/ihsan-sa/chip-flow/pull/5))
- d_cosim spike: M10 uses cocotbext-ams, not ivlng ([#4](https://github.com/ihsan-sa/chip-flow/pull/4))
- gLayout spike: take the gdsfactory fallback for M9 ([#3](https://github.com/ihsan-sa/chip-flow/pull/3))
- the eda launcher and the engine skeleton (M0, M1) ([#2](https://github.com/ihsan-sa/chip-flow/pull/2))
- the design the chip-flow milestones build from ([#1](https://github.com/ihsan-sa/chip-flow/pull/1))
