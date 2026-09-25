# /ade session log - comparator
- P0: task_router -> full-run (exit 0). state.py init blocks/comparator; brief/spec.md copied verbatim from ./spec.md. Decision recorded: H1/H2 user pre-approved. P0 digest written; set-phase P1.
- P1: spawned spec-writer (fable/high) -> spec/spec.md, spec/spec.yaml (4 measures: vdiff_pos, vdiff_neg, tdelay, vreset; post_layout_bounds; no mc). Gate spec_lint: PASS (committed a5e8783).
- P2: spawned analog-designer TOPOLOGY MODE (fable/high) -> spec/topology.md (StrongARM template, 11 devices confirmed, spec.yaml unchanged). Gate spec_lint: PASS (committed 8d30398).
- P3: spawned bench-writer (opus/high, fresh, spec-only) -> tb/comparator_tb.cir + .bounds.json (4 measures). Gate spec_lint: PASS (committed 6312891). Decision: input CM=VDD/2. Note to P4: template polarity is inverted vs spec.
- P4: spawned analog-designer NETLIST MODE (opus/high) -> netlist/comparator.cir (input-pair drains crossed for spec polarity), sizing/sizing.yaml (template defaults, bounded). Gates netlist_lint: PASS (fcfa56e), sim_tt: PASS (cf16418), sim_pvt: PASS (73559e9); worst tdelay 0.394 ns sf, fastest 0.203 ns ff.

## Resume 1 (P4 -> release)
- Resume: task_router -> resume; state.py resume: P4, 5 gates fresh, next mc, no issues/jobs; logged resumed.
- mc: not applicable (no mc.enabled), exit 2 unrecorded (known limit); gate.py still committed 2c7f80f "mc pass".
- P4 digest written (log/P4-digest.md).
- H1: spawned reviewer (fable/high, fresh) -> VERDICT reject. Recorded H1 rejected (pre-approval not applied: gate evidence invalid).
- ENGINE DEFECT (STOP): bench_strength builds mutant decks without sizing. engine/scripts/check_bench_strength.py:63 calls sim_run.build_subs(..., {}) where sim_run.py:193/206 passes load_sizing(ws). Every mutant deck reads "* no sizing overrides" (e.g. log/bench_strength/xmtail_size_doubled_w__comparator_tb__tt.cir line 17); ngspice exits 13 with "Undefined parameter [w_tail]", and the gate counts engine errors as kills (docstring lines 13-15), so 33/33 "killed" is hollow. Repro: `$CFH/bin/eda ngspice -b blocks/comparator/log/bench_strength/xmtail_size_doubled_w__comparator_tb__tt.cir`. Reviewer's hand re-run with sizing injected: 2 survivors (xmtail_connection_removed, xmtail_size_doubled_w); scratch under /tmp/h1-review-mutants/. bench_strength pass (33f15db) is not evidence. P5 not started.
- Open for owner (from H1): 1 mV bench overdrive vs unchanged post_layout vdiff bounds - 0.2 fF dn asymmetry flips the decision, so pex_sim likely fails on any layout; spec/topology.md device table shows template (uncrossed) wiring.

## Resume 2 (after bench_strength engine fix)
- Note: gate.py titled the n/a mc commit "ade comparator: mc pass" (2c7f80f) though mc was not applicable and not recorded.

## Resume 3 (fixed bench_strength -> release)
- Resume: task_router -> resume (exit 0); state.py resume: P4, fresh spec_lint/netlist_lint/sim_tt/sim_pvt, next bench_strength (last recorded: fail attempt 2, 31/33 killed, survivors xmtail connection_removed + size_doubled_w). jobs.py: none running. Logged resumed.
- Uncommitted log/bench_strength/*.cir diffs from Resume 2's run left in tree (engine output, not design files).
- Gate bench_strength (fixed engine, re-run attempt 3): FAIL 31/33 killed; survivors xmtail_connection_removed, xmtail_size_doubled_w (as expected). Mutant decks now all carry sizing (no 'no sizing overrides' deck). Recorded; not committed (fail).
- Fix loop bench_strength a1: budget consumed (2 left), snapshot pre-fix-bench_strength-a1, fix_dispatch -> 1 order (wo-1, testbench, both survivors) -> issue 1 fixing. Spawned bench-writer WORK-ORDER MODE (opus/high, fresh).

## Resume 4 (bench_strength a1 -> release)
- Resume: state.py resume: P4, 4 gates fresh, next bench_strength, issue 1 fixing (wo-1, bench-writer); no jobs. Logged resumed.
- Re-dispatched wo-1: spawned bench-writer WORK-ORDER MODE (opus/high, fresh); scope narrowed to xmtail_size_doubled_w (xmtail_connection_removed now owner-accepted equivalent).

## Resume 5 (bench_strength a1, wo-1 re-spawn -> release)
- Resume: state.py resume: P4, fresh spec_lint/netlist_lint/sim_tt/sim_pvt, next bench_strength, issue 1 fixing (wo-1); no jobs. Logged resumed.
- Checked: spec_lint's bench cross-check (speclib.lint_measures_vs_bench_bounds) fails a tb bound the spec never names, so a new measure (e.g. peak supply current) needs a spec_edit (router: spec verb, human_hold 2, stales every gate).
- Re-dispatched wo-1: spawned bench-writer WORK-ORDER MODE (opus/high, fresh), scope xmtail_size_doubled_w only; told of the cross-check constraint.

## Resume 6 (bench_strength a1 -> release)
- Resume: task_router -> resume (exit 0); state.py resume: P4, fresh spec_lint/netlist_lint/sim_tt/sim_pvt, next bench_strength (attempt 6 fail, 32/33 killed, 1 survivor xmtail_size_doubled_w; xmtail_connection_removed accepted equivalent in-gate), issue 1 fixing (wo-1); jobs.py: none. Logged resumed. tb/ unchanged since 6312891 (no prior bench-writer wrote anything).
- wo-1 cannot be closed in tb/ alone: every tb bound must be a spec measure (spec_lint cross-check), and none of the 4 spec measures sees the tail's width. Routed spec verb (task_router --verb spec, exit 0: spec_edit, human_hold 2, stales all gates). Recipe step names spec-writer tier opus/high; SKILL.md tier table says fable/high - used fable/high. Spawned spec-writer REVISION (fable/high, fresh, foreground): add a tail-current measure.
- spec-writer: spec.yaml +itail_peak (peak |i(vss pin)| 5-7 ns, 1.0-3.2 mA, corners [tt], hand estimate ~2.5 mA typ, ~1.6x uncertain); spec.md "Current draw" subsection; nothing else moved. spec_lint (no commit): FAIL 1, measure_no_bench_bound itail_peak (expected). Declared spec_edit (human_hold 2 -> summarize at H1).
- Re-dispatched wo-1: spawned bench-writer WORK-ORDER MODE (opus/high, fresh, foreground): add itail_peak to tb/ per spec.
- bench-writer: tb +vsns 0 V ammeter in DUT vss lead, .measure itail_peak (max |i(vsns)| 5-7 ns), bounds copied from spec. Its no-commit runs: spec_lint pass, sim_tt pass (itail_peak tt 2.146 mA), bench_strength pass 33/33 (doubled tail 3.884 mA). OPEN: per-measure corners [tt] not honoured by sim_run - sidecar bounds checked at every corner.
- Gates (recipe order, --commit): spec_lint PASS 12eac67; netlist_lint PASS 0a43777; sim_tt PASS f12c68c.
- sim_pvt PASS 744c071 (itail_peak tt 2.146, ss 1.756, ff 2.753, sf 1.717, fs 2.692 mA - all inside 1.0-3.2 mA, so the ignored per-measure corners [tt] did not change the result; noted, not a STOP: the engine over-checks, never under-checks).
- bench_strength PASS b3f4164 (33/33, 0 survivors; xmtail_size_doubled_w killed, itail_peak 3.88 mA > 3.2; xmtail_connection_removed accepted equivalent). Issue 1 -> fixed. Next: mc.
- mc: not applicable (spec has no mc.enabled), exit 2 unrecorded (known limit); gate.py again committed b23eecb 'mc pass' on the unrecorded result (as 2c7f80f).
- P4 digest rewritten (8 lines). H1: spawned reviewer (fable/high, fresh, foreground).
- H1: reviewer VERDICT approve (re-sim confirms kill 3.884 mA vs 3.2, decks carry sizing). OPEN low: spec.md itail derivation (clock-edge spike, edge-rate dependent) and "min catches narrow tail" claim loose; Reference tdelay stale; per-measure corners unhonoured. Medium P5: 1 mV overdrive vs pex asymmetry. Recorded H1 approved.
- set-phase P5: refused naming only mc (P4); decision recorded, --force.
- P5: spawned in parallel (foreground) bench-writer PEX MODE (opus/high, fresh) and layout-writer (opus/high, fresh).
- bench-writer PEX: layout_ref/comparator_pex_tb.{cir,bounds.json}, bounds = post_layout_bounds exactly, 1 mV kept. layout-writer: layout/gen_comparator.py (mirror-symmetric, 3 small asymmetries noted); layout_gen ran clean; did not run drc/lvs (role forbids).
- Gates: drc PASS 5ea44c9 (0 findings). lvs FAIL (not committed): netgen "property type mismatch ... unresolved expression" on l/w (e.g. "2.8e-07", "4e-06"); facts.sizing_applied {} - the reference netlist's {w_*}/{l_*} .param sizing is not substituted (SKILL.md known limit, here as a hard fail). Suspected ENGINE DEFECT (STOP, not worked around). Repro: `$CFH/bin/eda python $CFH/engine/scripts/gate.py --gate lvs --skill ade --workspace blocks/comparator`; see blocks/comparator/log/lvs/netgen_lvs.log.
- pex_sim FAIL (not committed): vdiff_neg = +3.3 V vs <= -2.5 (second decision flipped), vdiff_pos 3.3, tdelay 0.462 ns (<=0.5), vreset 3.3; parasitics r 71, c 73. The H1 medium risk: 1 mV overdrive vs extracted asymmetry. Needs layout-fixer (symmetry) and/or an owner decision on the post-layout overdrive.
- STOPPED here: session USD budget nearly spent (~$0.6 left); fix loops for lvs/pex_sim, H2 and release not run. Phase P5.

## Resume 7 (P5 -> release)
- Resume: state.py resume: P5, fresh spec_lint/netlist_lint/sim_tt/sim_pvt/bench_strength/drc, no open issues, no jobs. Logged resumed. lvs engine defect (sizing .param not substituted) reported fixed in engine.
- Gate lvs (fixed engine, attempt 2): PASS 705c0bd; sizing_applied lists all 9 (w_tail 10u, l_tail 0.28u, w_in 8u, l_in, w_latch_n 4u, w_latch_p 6u, l_latch, w_reset 4u, l_reset).
- pex_sim fix loop a1: budget consumed (2 left), snapshot pre-fix-pex_sim-a1, fix_dispatch -> 1 order (wo-2, layout, measure_out_of_bounds vdiff_neg) -> issue 2 fixing. Spawned layout-fixer (opus/high, fresh, foreground).
- layout-fixer wo-2 (2 attempts): outn/outp cross-coupling tracks not mirrored (outp +0.152 fF). a1 alternated tracks -> vdiff_neg -3.3 but tdelay 0.506 ns > 0.5 (old pass leaned on asymmetry); a2 spaced outn/outp buses (coupling 2.44->0.98 fF, each output 13.38 fF, residual 3 aF). Its no-commit runs: drc/lvs/pex_sim pass, tdelay 0.488 ns. OPEN: tdelay 2.4% margin at tt only.
- declared layout_code_edit. Gates (--commit): drc PASS 75c2f63 (0); lvs PASS 3d6a4bb (sizing_applied 9); pex_sim PASS efdd9a0 (vdiff_pos +3.3, vdiff_neg -3.3, tdelay 0.488 ns <= 0.5, vreset 3.30). Issue 2 -> fixed.
- P5 digest written (6 lines). H2: spawning reviewer (fable/high, fresh, foreground).
- reports/gate-{drc,lvs,pex_sim}.json rewritten from the --commit runs' JSON (disk copies were attempt-1 fails; gate.py writes reports/ only with --out).
- H2: reviewer (fable/high, fresh) VERDICT reject. All gates fresh-pass on its re-run; lvs sizing_applied = sizing.yaml 9/9; post_layout_bounds == pex bounds; wo-2 is a genuine symmetry fix (outputs 13.382/13.379 fF). Reject reason: its hand corner sims of the extracted netlist give tdelay ss 0.639 / sf 0.648 ns (> 0.5 post-layout, > 0.6 pre-layout); spec bounds post-layout at tt only, so gates are honestly green but tt margin is 12 ps. Low: topology.md drain naming stale; sim_run over-checks per-measure corners. Recorded H2 rejected.
- STOPPED: H2 rejected on a question only the owner can answer (accept typical-only post-layout tdelay, or require slow-corner post-layout tdelay -> speed fix/spec change). Not forcing P6. Phase P5; release not run.

## Resume 8 (H2 rejection loop -> release)
- Resume: state.py resume: P5, all 8 gates fresh, no issues/jobs. Logged resumed. Decision recorded: H2 rejection stands; loop with constraint post-layout tdelay <= 0.6 ns at ss and sf (reviewer hand sims ss 0.639 / sf 0.648 ns); no bound weakened.
- task_router: ambiguous (layout, resize) -> --verb resize (exit 0; sizing_edit; gates sim_tt, sim_pvt, bench_strength, mc, lvs, pex_sim, release; human_hold 1). Snapshot pre-resize-r8. Spawned analog-designer resize (opus/high, fresh, foreground), proxy = pre-layout bench + ~13.4 fF lumped per output at ss/sf.
- analog-designer: sizing.yaml only (w_latch_p 6u->3u, w_reset 4u->2u at min). Pre-layout tdelay tt 0.172 ss 0.230 sf 0.259 fs 0.126 ns; itail_peak max ff 2.553 mA. Proxy (diffusion ad/as + old wiring C) calibrated vs extracted 0.488/0.639/0.648 -> new tt 0.333 ss 0.457 sf 0.500 ns. Its no-commit bench_strength: FAIL 32/33, survivor xmtail_type_flipped (old kill was via tdelay 0.615 > 0.6, fragile).
- Declared sizing_edit. Gates (--commit): sim_tt PASS f4b01e6 (tdelay 0.172 ns, itail 2.092 mA); sim_pvt PASS d73d3bf (0 failing, 5 corners).
- bench_strength FAIL (32/33; survivor xmtail_type_flipped), recorded, not committed.
- Fix loop bench_strength r8a1: budget consumed (1 left), snapshot pre-fix-bench_strength-r8a1, fix_dispatch -> wo-3 (testbench, survivor_type_flipped) -> issue 3 fixing. No tb-only fix: a tighter vdiff_pos (>=3.0) fails ss where VDD=2.97, and a new tb bound must be a spec measure. Routed --verb spec (spec_edit, human_hold 2, stales all gates). Spawned spec-writer REVISION (fable/high, fresh, foreground): add a reset-phase supply current measure.
- spec-writer: spec.yaml +ireset (avg |i(vdd)| 12.5-14.5 ns, max 5 uA, default corners, est ~0.5 uA, fault 10-50 uA); nothing else moved; its spec_lint fails only measure_no_bench_bound ireset. Declared spec_edit (human_hold 2 -> summarize at H2).
- Spawned in parallel (foreground, fresh): bench-writer WORK-ORDER wo-3 (opus/high): add ireset to tb/; layout-fixer (opus/high): generator follows new sizing (w_latch_p 3u, w_reset 2u) + keep output cap low/symmetric.
- bench-writer wo-3: tb +ireset (.measure avg abs(i(vdd)) 12.5-14.5 ns, max 5 uA). No-commit runs: spec_lint/sim_tt/sim_pvt/bench_strength pass; ireset good 0.16-0.33 uA all corners; flipped-tail mutant 424.8 uA (killed).
- layout-fixer: no edit (generator reads widths from sizing.yaml). No-commit drc/lvs/pex_sim pass; extracted tdelay tt 0.360 ss 0.488 sf 0.528 ns; outputs 12.264/12.254 fF, coupling 0.922 fF. No layout_code_edit declared (nothing changed).
- Gates (recipe order, --commit): spec_lint PASS aeb9502; netlist_lint PASS b256479; sim_tt PASS 90e278a; sim_pvt PASS 2b2b322; bench_strength PASS 921c268 (flipped tail killed); mc n/a exit 2 unrecorded (known limit; gate.py again committed 0e3dc53 "mc pass"); drc PASS 3c49c59; lvs PASS f5fc6df; pex_sim PASS 02f7e19 (tt tdelay 0.360 ns). Issue 3 -> fixed.
- P5 digest rewritten (6 lines). H2: spawned reviewer (fable/high, fresh, foreground) incl. extracted-netlist ss/sf corner check.
- H2: reviewer (fable/high, fresh) VERDICT approve. Extracted tdelay tt 0.360 / ss 0.488 / sf 0.528 ns (all decide to rail; ireset extracted 0.48-0.71 uA); lvs sizing 9/9; post_layout_bounds == pex bounds; no bound weakened (diff since efdd9a0 additive). ireset spec change (human_hold 2) summarized at H2: additive, reviewer endorses; human_hold 2 is summarize-only and H1/H2 were user pre-approved (P0), so not held. LOW: mc n/a unrecorded (known). Recorded H2 approved.
- set-phase P6: refused naming only mc (P4), as the SKILL.md known limit describes. The documented next step (state.py decision + set-phase P6 --force) was DENIED by this session's permission classifier (auto mode, 'Blind Apply'); not worked around. Phase stays P5 with H2 approved. Needs the user: allow the forced advance past mc (not applicable), then run release.
- release gate run WITHOUT --commit and without advancing phase (check only): PASS (0 failing) - attest sees every owed gate fresh-pass, mc n/a. Not committed; phase P5. STOPPED here pending the user's permission for the forced set-phase P6 (then: set-phase P6 --force, release --commit, set-phase done).

## Resume 9 (P5 -> release)
- Engine fix (not a force): state.py set-phase now re-asks the spec and skips mc when it declares no Monte Carlo, as release does. set-phase P6 PASS with no --force.
- release PASS 76c687c (gates-green, 0 waived). set-phase done.
