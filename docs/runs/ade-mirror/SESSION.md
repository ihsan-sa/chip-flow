# /ade session log - mirror
- Task: "design a current mirror from spec.md, all the way to release."
- Routed: task_router.py -> full-run (exit 0). Workspace blocks/mirror, state.py init OK (P0).
- P0: brief/spec.md copied verbatim; P0 digest written; set-phase P1.
- P1: spawned spec-writer (fable/high) -> spec.md/spec.yaml; iout_raw [18,32] uA, vdd 3.3 V assumed (decision recorded). Gate spec_lint: PASS (committed). set-phase P2.
- P2: spawned analog-designer TOPOLOGY (fable/high) -> topology.md, devices xmref/xmout (current_mirror.sp). Gate spec_lint: PASS. set-phase P3.
- P3: spawned bench-writer (opus/high, fresh) -> tb/mirror_tb.cir + bounds. Gate spec_lint: PASS (cross-check). Decision: iout held at VDD/2. set-phase P4.
- P4: spawned analog-designer NETLIST (opus/high) -> netlist/mirror.cir, sizing.yaml (w_ref 4u, l_ref 1u, w_out 8u, l_out 1u).
- ENGINE DEFECT (confirmed): gate.py sim_tt/sim_pvt with a RELATIVE --workspace (blocks/mirror, the form SKILL.md shows) fail "iout_raw never printed": sim_run renders {{NETLIST}} relative while ngspice runs with cwd=log/sim. Reproduced: relative -> FAIL, absolute -> PASS. Workaround (no patch): all recorded gates run with absolute workspace path from here on.
- Gates: netlist_lint PASS, sim_tt PASS, sim_pvt PASS (5/5 corners; tt 20.80, worst ss/sf 20.13 uA vs min 18).
- Gate bench_strength: FAIL, 4/6 killed; survivors xmout_size_doubled_w, xmref_size_doubled_w.
- Fix loop 1 (bench_strength): budget consumed (2 left), snapshot pre-fix-bench_strength-a1, fix_dispatch -> wo-1 (testbench) -> spawned bench-writer WORK-ORDER (opus/high). Result: no tb/ edit; diagnosed ENGINE DEFECT. Issue 1 escalated.
- ENGINE DEFECT #2 (blocking): engine/lib/netlistlib.py _size_mutant, non-numeric branch, appends "*2" after a brace expression -> `w={w_out}*2`. ngspice silently ignores it; mutant iout_raw = 20.7965 uA, identical to baseline. A true doubling (w={w_out*2} / w={w_ref*2}) gives 41.76 / 10.30 uA, outside [18,32] -> would be killed by the existing bench. Same bug on the `l` branch. Gate also should refuse (mutation-did-not-apply) rather than report a survivor and route to the bench-writer. Brace-param sizing is the normal form (sizing.yaml), so any /ade block hits this.
- STOPPED here per instructions (engine bug, not a design problem). Not patched. No H1/layout/release attempted. Resume: after the fix, `/ade --resume blocks/mirror`, re-run bench_strength with an ABSOLUTE workspace path.

## Resume (after upstream fixes)
- Both engine defects were fixed upstream in the checkout between the stop and this resume (coordinator): _size_mutant now emits w={(expr)*2}; sim_run resolves the netlist path (relative --workspace works).
- Resume: state.py resume -> P4, 4 gates fresh, issue 1 escalated. Gate bench_strength (relative path, confirms both fixes): PASS 6/6 killed. Issue 1 -> fixed.
- Gate mc: applicable:false, result not recorded (SKILL.md Known limit).

## Resume 2
- Routed: task_router.py "--resume blocks/mirror" -> resume (exit 0). state.py resume: P4, 5 gates fresh-pass, no stale, no open issues; jobs.py status: no jobs. Logged event resumed.
- H1: fresh reviewer (spawned before the stop) -> log/H1-review.md, APPROVE. Human H1 approved (user pre-approval) recorded. Decision recorded: mc not applicable (applicable:false).
- set-phase P5: refused naming only mc (P4) -> --force per SKILL.md Known limit (decision recorded). Now P5.
- P5: spawned bench-writer PEX MODE (opus/high, fresh) -> layout_ref/mirror_pex_tb.cir + bounds.
- bench-writer PEX returned: layout_ref/mirror_pex_tb.cir + bounds, iout_raw [18,32] uA from post_layout_bounds; dry-run on schematic 20.80 uA. OPEN: no AC/pole measure (spec names none).
- P5: spawned layout-writer (opus/high) -> layout/gen_mirror.py; termination: layout_gen.py clean.
- layout-writer returned: layout/gen_mirror.py, 2 single-finger nfets 4u/1u + 8u/1u, metal1 routing, psub tap; layout_gen.py exit 0. OPEN: vdd is a lone labelled pad (no device uses it).
- Declared layout_code_edit (initial generator).
- Decision recorded (layout_code_edit human_hold 1): initial generator accepted for the layout gates.
- Gate drc (detached, relative --workspace blocks/mirror, the SKILL.md form): ERROR exit 2, not recorded. klayout: "input file /tmp/ade-run-mirror/blocks/mirror/log/blocks/mirror/layout/mirror.gds does not exist".
- ENGINE DEFECT #3 (blocking, same class as #1): relative --workspace breaks the klayout DRC run. engine/scripts/check_analog_drc.py:59 `ws = Path(args.workspace)` is never resolved, so gds_path and rdb_path stay relative (blocks/mirror/layout/mirror.gds, blocks/mirror/log/analog_drc.lyrdb); engine/lib/layoutlib.py:335 run_klayout_drc then runs klayout with cwd=Path(out_rdb).parent (= blocks/mirror/log), so `-rd input=`/`report=` resolve under log/blocks/mirror/... Repro: cd /tmp/ade-run-mirror; `eda python engine/scripts/gate.py --gate drc --skill ade --workspace blocks/mirror` -> exit 2 (runlogs/drc1.log); `eda python engine/scripts/check_analog_drc.py --workspace /tmp/ade-run-mirror/blocks/mirror` -> status pass, 0 violations (runlogs/drc-abs-repro.json). Fix: resolve ws (or gds/rdb) to absolute in check_analog_drc.run, or pass absolute paths in run_klayout_drc. Likely also in check_lvs.py:101 and check_pex_sim.py:102 (same unresolved `ws = Path(args.workspace)`; magic/netgen run with cwd=work_dir at layoutlib.py:453/503) - not verified.
- Minor: check_analog_drc stdout carries a stray "(22, 0)" line before its JSON (JSON-out contract); source not traced (engine layout_gen path or the generator - orchestrator does not open the generator).
- STOPPED here per instructions (engine defect). Not patched. Phase P5; drc/lvs/pex_sim unrecorded; release not attempted. Resume: after the fix, `/ade --resume blocks/mirror`, run drc, lvs, pex_sim, then H2 and P6. The generator already passes klayout DRC (0 findings) when run with an absolute path.

## Resume 3 (after upstream fix of defect #3)
- Seam: coordinator fixed defect #3 upstream: gate.py resolves --workspace before any check; check_analog_drc/check_analog_lvs/check_pex_sim resolve it too; layout_gen.build sends generator stdout to stderr (the stray "(22, 0)").
- Resume: P5, 5 P4 gates fresh-pass, none stale, no open issues. Logged resumed.
- Gate drc (relative workspace, confirms fix #3): PASS 0 findings, recorded, committed 18257fa. '(22, 0)' now goes to stderr.
- Gate lvs: PASS 0 findings, recorded, committed ff3d37a. Note: facts.sizing_applied is {} (sized_reference found no sizing name on the .subckt line) - to be checked by the H2 reviewer.
- Gate pex_sim: PASS, iout_raw post-layout 20.79 uA in [18,32] uA (7 R, 8 C extracted), recorded, committed 81cf655.
- P5 digest written. Spawned reviewer (fable/high, fresh context) for H2 -> log/H2-review.md.

## Resume 4 (H2 approval, P6 release)
- Routed: task_router.py "--resume blocks/mirror" -> resume (exit 0). state.py resume: P5, 8 gates fresh-pass, no stale, no open issues, disposition gates-green; jobs.py status: no jobs. Logged event resumed.
- H2 OPEN (reports missing): restored reports/gate-{drc,lvs,pex_sim}.json verbatim from the recorded runs' stdout (input_digest = current layout hash); no gate re-run (passed+fresh). Decision recorded.
- H2: reviewer (fresh, fable/high) APPROVE -> log/H2-review.md. Human H2 approved (user pre-approval) recorded.
- set-phase P6: refused naming only mc (P4) -> decision recorded, --force per SKILL.md Known limit. Now P6.
- P6: gate release (strict, --out reports/gate-release.json): PASS 0 failing, disposition gates-green, reports/checks.json written; committed 266b72f (with restored P5 reports, H2 review, P5 digest).
- attest.py build: pass (checks.json rebuilt); attest.py verify: pass; attest.py disposition: gates-green, mc recorded ran:false/ok:true "not applicable" (spec has no mc.enabled).
- P6 digest written. set-phase done: refused naming only mc (P4) -> decision recorded, --force per Known limit. Phase done; state.py resume: disposition gates-green, no stale gates.
- Committed b0ef301 (P6 digest, gate-release.json, checks.json rebuild, state). Block released.
- OPEN (engine, medium, from H2; not this block's result): check_analog_lvs.py sized_reference substitutes sizing only on the .subckt line, not .param lines, and silently accepts an empty sizing_applied - a later resize/optimise would make LVS compare against stale netlist defaults. Not patched.
