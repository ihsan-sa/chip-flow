"""engine/scripts/state.py: state.json v3 - schema, freshness, gate
recording, edit classes, jobs, holdout, snapshots. Hermetic.

The M1 done criterion (docs/design.md, "### M1."): tests/fixtures/ws-empty
goes through state.py init, edit --class rtl_edit, freshness and resume
with the marks section 1.6 lists.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
FIXTURES = REPO / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import state as state_mod  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402
import safelib  # noqa: E402


def ws_empty(tmp_path: Path) -> Path:
    dest = tmp_path / "ws-empty"
    shutil.copytree(FIXTURES / "ws-empty", dest)
    return dest


# --------------------------------------------------------------- lifecycle

def test_init_writes_v3_schema_and_scaffold(tmp_path):
    ws = ws_empty(tmp_path)
    st = state_mod.State.init(ws, "vde", "counter8")
    assert st.data["version"] == 3
    assert st.data["skill"] == "vde"
    assert st.data["block"] == "counter8"
    assert st.data["phase"] == "P0"
    for field in ("toolchain", "gates", "jobs", "holdout", "optimise",
                  "human", "artifacts", "open_issues", "budgets",
                  "decisions", "edits", "spawns", "history"):
        assert field in st.data
    for d in state_mod.SUBDIRS:
        assert (ws / d).is_dir()


def test_init_refuses_unknown_skill(tmp_path):
    with pytest.raises(CheckError, match="unknown skill"):
        state_mod.State.init(ws_empty(tmp_path), "pcbde", "x")


def test_init_twice_refuses_without_force(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    with pytest.raises(CheckError, match="already exists"):
        state_mod.State.init(ws, "vde", "counter8")
    state_mod.State.init(ws, "vde", "counter8", force=True)  # ok


def test_save_is_atomic_compare_and_swap(tmp_path):
    ws = ws_empty(tmp_path)
    st1 = state_mod.State.init(ws, "vde", "counter8")
    st2 = state_mod.State.load(ws / "state.json")
    st1.add_decision("a", "b")
    st1.save()
    st2.add_decision("c", "d")
    with pytest.raises(safelib.StaleWriteError):
        st2.save()


# ------------------------------------------------------ M1's own scenario

def test_ws_empty_init_edit_freshness_resume(tmp_path):
    """The exact M1 scenario: state.py init, edit --class rtl_edit,
    freshness and resume, with the marks section 1.6 lists."""
    ws = ws_empty(tmp_path)
    r1, out = state_mod.run(["init", "--workspace", str(ws), "--skill", "vde",
                            "--block", "counter8"])
    assert r1["skill"] == "vde" and r1["block"] == "counter8"

    # a gate must be RECORDED for its stale mark to have somewhere to land
    (ws / "rtl").mkdir(exist_ok=True)
    (ws / "rtl" / "counter8.v").write_text("module counter8; endmodule\n",
                                           encoding="utf-8")
    result_path = ws / "lint-result.json"
    result_path.write_text(json.dumps({"status": "pass", "failing_count": 0,
                                       "counts": {"total": 0}}),
                           encoding="utf-8")
    r2, _ = state_mod.run(["record-gate", "--workspace", str(ws),
                          "--gate", "lint", "--result", str(result_path)])
    assert r2["status"] == "pass"

    r3, _ = state_mod.run(["edit", "--workspace", str(ws),
                          "--class", "rtl_edit", "--note", "hand-wrote rtl"])
    marked = set(r3["edit"]["gates_marked"])
    # section 1.6: rtl_edit marks lint, sim, holdout, mutate, formal, cover,
    # synth, harden, timing, drc, lvs, glsim, precheck, release - but only
    # gates with a RECORDED result can carry a mark (an unrun gate has no
    # result to distrust); only `lint` was recorded above.
    assert marked == {"lint"}
    assert set(r3["edit"]["gates"]) == {
        "lint", "sim", "holdout", "mutate", "formal", "cover", "synth",
        "harden", "timing", "drc", "lvs", "glsim", "precheck", "release"}

    r4, _ = state_mod.run(["freshness", "--workspace", str(ws)])
    assert r4["gates"]["lint"]["fresh"] is False  # hash valid, but marked
    assert "lint" in r4["summary"]["stale"]

    r5, _ = state_mod.run(["resume", "--workspace", str(ws)])
    assert r5["skill"] == "vde" and r5["block"] == "counter8"
    assert "lint" in r5["gates_passed"]          # last recorded status
    assert "lint" not in r5["gates_passed_fresh"]  # but not fresh (marked)
    assert r5["gates_stale"] == ["lint"]


def test_resume_summary_open_issues_includes_escalated_not_fixed_or_waived(tmp_path):
    """Same rule as attest.py's build() and task_router.py's resume_view
    (M5, found running the fix loop for real): a resumed session must still
    see an escalated (human-decision-pending) issue, but not one genuinely
    closed as fixed or (properly) waived."""
    ws = ws_empty(tmp_path)
    st = state_mod.State.init(ws, "vde", "counter8")
    a = st.open_issue({"gate": "mutate", "fixer": "testbench"})
    b = st.open_issue({"gate": "lint", "fixer": "rtl"})
    c = st.open_issue({"gate": "cover", "fixer": "testbench"})
    st.update_issue(a["id"], status="escalated")
    st.update_issue(b["id"], status="fixed")
    st.update_issue(c["id"], status="waived", note="x", approved_by="alice")
    st.save()
    summary = st.resume_summary()
    ids = {i["id"] for i in summary["open_issues"]}
    assert ids == {a["id"]}


def test_issue_superseded_by_a_fixed_issue_of_the_same_gate(tmp_path):
    """A finding re-dispatched as a new issue (the first work order refused
    by a guard) closes the old one only by pointing at the fixed
    replacement - never without a fix or a waiver of its own."""
    ws = ws_empty(tmp_path)
    st = state_mod.State.init(ws, "vde", "uart")
    old = st.open_issue({"gate": "glsim", "fixer": "rtl"})
    new = st.open_issue({"gate": "glsim", "fixer": "rtl"})
    other = st.open_issue({"gate": "lint", "fixer": "rtl"})
    st.update_issue(other["id"], status="fixed")
    for kw in ({},                          # no --by
               {"by": 999},                 # no such issue
               {"by": old["id"]},           # itself
               {"by": other["id"]},         # fixed, but another gate
               {"by": new["id"]}):          # same gate, but not fixed yet
        with pytest.raises(CheckError):
            st.update_issue(old["id"], status="superseded", **kw)
    with pytest.raises(CheckError, match="--by"):
        st.update_issue(old["id"], status="fixed", by=new["id"])
    assert old["status"] == "open"

    st.update_issue(new["id"], status="fixed")
    rec = st.update_issue(old["id"], status="superseded", by=new["id"])
    assert rec["superseded_by"] == new["id"] and rec["closed"]
    assert st.resume_summary()["open_issues"] == []

    # the replacement reopened: the superseded one counts as open again
    st.update_issue(new["id"], status="fixing")
    ids = {i["id"] for i in st.resume_summary()["open_issues"]}
    assert ids == {old["id"], new["id"]}


def test_issue_superseded_through_the_cli(tmp_path):
    ws = ws_empty(tmp_path)
    st = state_mod.State.init(ws, "vde", "uart")
    old = st.open_issue({"gate": "glsim", "fixer": "rtl"})
    new = st.open_issue({"gate": "glsim", "fixer": "rtl"})
    st.update_issue(new["id"], status="fixed")
    st.save()
    r, _ = state_mod.run(["issue", "--workspace", str(ws), "--id",
                          str(old["id"]), "--status", "superseded",
                          "--by", str(new["id"]), "--note", "re-dispatched"])
    assert r["issue"]["status"] == "superseded"
    assert r["issue"]["superseded_by"] == new["id"]


def test_formal_gate_hash_covers_a_spec_yaml_depth_edit(tmp_path):
    """invalidation.yaml's `formal: [rtl, formal, spec_yaml]` (M5 - commit
    "invalidation: formal's own depth is a real gate input"):
    check_formal.py reads spec.yaml's own `formal: {depth}` field too, so a
    depth-only edit - no file under rtl/ or formal/ touched at all - must
    still show up as an input-hash change on the `formal` gate. Without
    spec_yaml in this gate's own input list, a recorded pass would keep
    reading fresh after the depth it actually ran against had moved."""
    ws = ws_empty(tmp_path)
    state_mod.run(["init", "--workspace", str(ws), "--skill", "vde",
                  "--block", "counter8"])
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nformal:\n  depth: 20\n",
        encoding="utf-8")
    (ws / "formal" / "counter8_formal.sv").write_text("// wrapper\n",
                                                       encoding="utf-8")
    (ws / "rtl" / "counter8.v").write_text("module counter8; endmodule\n",
                                           encoding="utf-8")
    result_path = ws / "formal-result.json"
    result_path.write_text(json.dumps({"status": "pass", "failing_count": 0,
                                       "counts": {"total": 0}}),
                           encoding="utf-8")
    state_mod.run(["record-gate", "--workspace", str(ws), "--gate", "formal",
                  "--result", str(result_path)])
    fresh_before, _ = state_mod.run(["freshness", "--workspace", str(ws)])
    assert fresh_before["gates"]["formal"]["fresh"] is True

    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nformal:\n  depth: 40\n",
        encoding="utf-8")
    fresh_after, _ = state_mod.run(["freshness", "--workspace", str(ws)])
    assert fresh_after["gates"]["formal"]["hash_valid"] is False
    assert "spec_yaml" in fresh_after["gates"]["formal"]["changed_inputs"]


@pytest.mark.parametrize("gate", ["harden", "timing", "drc", "lvs", "glsim",
                                  "precheck"])
def test_m4_gate_hash_covers_a_spec_yaml_period_edit(tmp_path, gate):
    """harden reads clock.period_ns and tt_pins from spec.yaml, and the
    five signoff gates read `top` from it: a period-only edit, with nothing
    under rtl/ or harden/ touched, must stale a recorded pass."""
    ws = ws_empty(tmp_path)
    state_mod.run(["init", "--workspace", str(ws), "--skill", "vde",
                  "--block", "counter8"])
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nclock: {period_ns: 20}\n",
        encoding="utf-8")
    (ws / "rtl" / "counter8.v").write_text("module counter8; endmodule\n",
                                           encoding="utf-8")
    result_path = ws / f"{gate}-result.json"
    result_path.write_text(json.dumps({"status": "pass", "failing_count": 0,
                                       "counts": {"total": 0}}),
                           encoding="utf-8")
    state_mod.run(["record-gate", "--workspace", str(ws), "--gate", gate,
                  "--result", str(result_path)])
    fresh_before, _ = state_mod.run(["freshness", "--workspace", str(ws)])
    assert fresh_before["gates"][gate]["fresh"] is True

    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nclock: {period_ns: 10}\n",
        encoding="utf-8")
    fresh_after, _ = state_mod.run(["freshness", "--workspace", str(ws)])
    assert fresh_after["gates"][gate]["hash_valid"] is False
    assert "spec_yaml_sans_formal" in fresh_after["gates"][gate][
        "changed_inputs"]


VDE_GATES = ["spec_lint", "lint", "sim", "holdout", "mutate", "formal",
             "cover", "synth", "harden", "timing", "drc", "lvs", "glsim",
             "precheck", "release"]
VDE_SPEC = ("top: counter8\nrequirements: []\nclock: {period_ns: 20}\n"
            "cover: {line_min: 95}\nformal:\n  depth: 20\n")


def _vde_ws_with_passes(tmp_path):
    """A /vde workspace with every input present and a recorded pass on
    every gate."""
    ws = ws_empty(tmp_path)
    state_mod.run(["init", "--workspace", str(ws), "--skill", "vde",
                  "--block", "counter8"])
    files = {"spec/spec.yaml": VDE_SPEC,
             "rtl/counter8.v": "module counter8; endmodule\n",
             "tb/test_counter8.py": "# tb\n",
             "holdout/test_hold.py": "# holdout\n",
             "formal/counter8_formal.sv": "// wrapper\n",
             "harden/config.json": "{}\n",
             "harden/info.yaml": "project: {}\n"}
    for rel, text in files.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text, encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")   # one load/save: fast
    for g in VDE_GATES:
        st.record_gate(g, {"status": "pass", "failing_count": 0,
                           "counts": {"total": 0}})
    st.save()
    assert _not_fresh(ws) == set()
    return ws


def _not_fresh(ws):
    fresh, _ = state_mod.run(["freshness", "--workspace", str(ws)])
    return {g for g, v in fresh["gates"].items() if not v["fresh"]}


def test_vde_formal_key_only_edit_stales_only_formal_and_cover(tmp_path):
    """An edit to only spec.yaml's `formal:` key used to stale lint, sim,
    holdout, mutate and every later gate by hash, because each hashed the
    whole spec. Only formal and cover read it (cover takes the whole file),
    so only they go stale - by hash, and by the declared class's marks.
    spec_lint lints the whole file, so it re-runs too."""
    ws = _vde_ws_with_passes(tmp_path)
    (ws / "spec" / "spec.yaml").write_text(
        VDE_SPEC.replace("depth: 20", "depth: 40\n  cover_depth: 60"),
        encoding="utf-8")
    assert _not_fresh(ws) == {"spec_lint", "formal", "cover"}
    state_mod.run(["edit", "--workspace", str(ws),
                   "--class", "spec_formal_edit"])
    assert _not_fresh(ws) == {"spec_lint", "formal", "cover"}


@pytest.mark.parametrize("edit", [
    ("top: counter8", "top: counter9"),
    ("period_ns: 20", "period_ns: 10"),
    ("requirements: []", "requirements: [{id: R1, check: sim}]"),
    ("line_min: 95", "line_min: 80"),
])
def test_vde_other_spec_edits_still_stale_every_spec_reader(tmp_path, edit):
    """Any spec.yaml edit outside `formal:` still stales every vde gate
    that reads the spec - including cover and synth, which read `top` (and
    cover its thresholds) but used to have no spec input at all."""
    ws = _vde_ws_with_passes(tmp_path)
    (ws / "spec" / "spec.yaml").write_text(VDE_SPEC.replace(*edit),
                                           encoding="utf-8")
    assert _not_fresh(ws) == set(VDE_GATES) - {"release"}


def test_vde_non_mapping_spec_still_stales_every_spec_reader(tmp_path):
    """A spec.yaml that stops being a mapping hashes raw under both kinds,
    so it never passes for a formal-only edit."""
    ws = _vde_ws_with_passes(tmp_path)
    (ws / "spec" / "spec.yaml").write_text("- not a mapping\n",
                                           encoding="utf-8")
    assert _not_fresh(ws) == set(VDE_GATES) - {"release"}


def _ade_ws_with_passes(tmp_path, gates):
    """An /ade workspace with every input kind present and a recorded pass
    on each of `gates`."""
    ws = ws_empty(tmp_path)
    state_mod.run(["init", "--workspace", str(ws), "--skill", "ade",
                  "--block", "mirror"])
    files = {"spec/spec.yaml": "top: mirror\nfootprint_um: {width: 60, height: 60}\n",
             "netlist/mirror.cir": ".subckt mirror a b\n.ends\n",
             "tb/mirror_tb.cir": "* tb\n",
             "sizing/sizing.yaml": "w: {value: 1.0}\n",
             "layout/gen_mirror.py": "# gen\n",
             "layout_ref/mirror_pex_tb.cir": "* pex tb\n",
             "layout_ref/mirror_pex_tb.bounds.json": "{}\n"}
    for rel, text in files.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text, encoding="utf-8")
    result_path = ws / "result.json"
    result_path.write_text(json.dumps({"status": "pass", "failing_count": 0,
                                       "counts": {"total": 0}}),
                           encoding="utf-8")
    for g in gates:
        state_mod.run(["record-gate", "--workspace", str(ws), "--gate", g,
                      "--result", str(result_path)])
    fresh, _ = state_mod.run(["freshness", "--workspace", str(ws)])
    assert all(fresh["gates"][g]["fresh"] for g in gates), fresh["gates"]
    return ws


def _stale_by_hash(ws, kind):
    fresh, _ = state_mod.run(["freshness", "--workspace", str(ws)])
    return {g for g, v in fresh["gates"].items()
            if v.get("hash_valid") is False
            and kind in (v.get("changed_inputs") or [])}


ADE_GATES = ["netlist_lint", "sim_tt", "sim_pvt", "bench_strength", "mc",
             "drc", "lvs", "pex_sim", "release"]


def test_ade_sizing_edit_stales_every_gate_that_loads_sizing(tmp_path):
    """sim_run.load_sizing() feeds sim_tt, sim_pvt, bench_strength, mc and
    lvs; a sizing-only edit must stale their recorded passes by hash.
    pex_sim reads no sizing.yaml (the generator bakes sizes into the GDS),
    so the sizing_edit class's mark is what re-runs it."""
    ws = _ade_ws_with_passes(tmp_path, ADE_GATES)
    (ws / "sizing" / "sizing.yaml").write_text("w: {value: 2.0}\n",
                                               encoding="utf-8")
    assert _stale_by_hash(ws, "sizing") == {
        "sim_tt", "sim_pvt", "bench_strength", "mc", "lvs", "release"}
    state_mod.run(["edit", "--workspace", str(ws), "--class", "sizing_edit"])
    fresh, _ = state_mod.run(["freshness", "--workspace", str(ws)])
    assert fresh["gates"]["pex_sim"]["fresh"] is False


def test_ade_layout_ref_edit_stales_pex_sim(tmp_path):
    ws = _ade_ws_with_passes(tmp_path, ADE_GATES)
    (ws / "layout_ref" / "mirror_pex_tb.bounds.json").write_text(
        '{"measures": {}}\n', encoding="utf-8")
    assert _stale_by_hash(ws, "layout_ref") == {"pex_sim"}


def test_ade_spec_yaml_footprint_edit_stales_drc(tmp_path):
    ws = _ade_ws_with_passes(tmp_path, ["drc"])
    (ws / "spec" / "spec.yaml").write_text(
        "top: mirror\nfootprint_um: {width: 40, height: 60}\n",
        encoding="utf-8")
    assert "drc" in _stale_by_hash(ws, "spec_yaml")


def test_ade_layout_code_edit_covers_layout_ref():
    imap = statelib.load_map()
    cls = imap["edit_classes"]["ade"]["layout_code_edit"]
    assert "layout_ref" in cls["mutates"] and "pex_sim" in cls["gates"]


# --------------------------------------------------------- gate recording

def test_record_gate_requires_pass_or_fail(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    st = state_mod.State.load(ws / "state.json")
    with pytest.raises(CheckError, match="pass\\|fail"):
        st.record_gate("lint", {"status": "bogus"})


def approve(st, checkpoint):
    """Present `checkpoint` and record an approval that quotes its challenge,
    the way the person's reply at the checkpoint would."""
    chal = st.present_checkpoint(checkpoint)["challenge"]
    st.record_human(checkpoint, "approved", f"{chal} approve")


def test_set_phase_refuses_past_a_gate_with_no_result(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8", phase="P4")
    st = state_mod.State.load(ws / "state.json")
    approve(st, "H1")
    with pytest.raises(CheckError, match="cannot advance"):
        st.set_phase("P5")
    warnings = st.set_phase("P5", require_gates=False)
    assert any(w["kind"] == "gate_coverage" for w in warnings)
    assert st.data["phase"] == "P5"


def test_set_phase_passes_once_every_owed_gate_is_recorded(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8", phase="P1")
    (ws / "log" / "P1-digest.md").write_text("- did the spec\n", encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")
    st.record_gate("spec_lint", {"status": "pass"})
    st.save()
    st = state_mod.State.load(ws / "state.json")
    warnings = st.set_phase("P4")   # spec_lint (P1) is the only gate < P4
    assert warnings == []
    assert st.data["phase"] == "P4"


@pytest.mark.parametrize("mc_on", [False, True])
def test_set_phase_skips_mc_only_when_the_spec_declares_it_not_applicable(
        tmp_path, mc_on):
    # /ade: every gate before P5 passed except mc, which gate.py cannot
    # record when the spec asks for no Monte Carlo.
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "mirror", phase="P4")
    spec = (REPO / "corpus" / "ade" / "mirror" / "spec.yaml").read_text(
        encoding="utf-8")
    assert "\nmc:" not in spec
    if mc_on:
        spec += "\nmc:\n  enabled: true\n"
    (ws / "spec").mkdir(exist_ok=True)
    (ws / "spec" / "spec.yaml").write_text(spec, encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")
    for ph, g in state_mod.applicable_gate_order("ade"):
        if g != "mc" and state_mod.PHASES.index(ph) < state_mod.PHASES.index("P5"):
            st.record_gate(g, {"status": "pass"})
    approve(st, "H1")
    if mc_on:   # MC asked for and not run: still owed, still refused
        with pytest.raises(CheckError, match=r"mc \(P4\)"):
            st.set_phase("P5")
        assert st.data["phase"] == "P4"
    else:       # declared not applicable: advances with no --force
        st.set_phase("P5")
        assert st.data["phase"] == "P5"
        assert not any(h["event"] == "phase_forced"
                       for h in st.data["history"])



@pytest.mark.parametrize("mc_on", [False, True])
def test_resume_next_gate_skips_mc_only_when_the_spec_declares_it_not_applicable(
        tmp_path, mc_on):
    # Found scoring the mirror rung: every P4 gate but mc passed, the spec
    # asks for no Monte Carlo, and resume still named mc as the next gate.
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "mirror", phase="P4")
    spec = (REPO / "corpus" / "ade" / "mirror" / "spec.yaml").read_text(
        encoding="utf-8")
    if mc_on:
        spec += "\nmc:\n  enabled: true\n"
    (ws / "spec").mkdir(exist_ok=True)
    (ws / "spec" / "spec.yaml").write_text(spec, encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")
    for ph, g in state_mod.applicable_gate_order("ade"):
        if g != "mc" and state_mod.PHASES.index(ph) < state_mod.PHASES.index("P5"):
            st.record_gate(g, {"status": "pass"})
    nxt = st.resume_summary()["next_gate"]
    if mc_on:   # MC asked for and not run: it is still the next gate
        assert nxt == {"phase": "P4", "gate": "mc"}
    else:       # declared not applicable: resume moves on to P5's first gate
        assert nxt["gate"] != "mc" and nxt["phase"] == "P5"

# ------------------------------------------------------------------- jobs

def test_job_lifecycle(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    st = state_mod.State.load(ws / "state.json")
    jid, rec = st.start_job("harden", 12345, "log/jobs/job-1.log")
    assert rec["status"] == "running" and rec["pid"] == 12345
    st.save()

    st = state_mod.State.load(ws / "state.json")
    st.update_job(jid, status="done", result={"exit_code": 0})
    st.save()

    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert data["jobs"][jid]["status"] == "done"
    assert data["jobs"][jid]["finished"] is not None
    assert data["jobs"][jid]["result"] == {"exit_code": 0}


def test_update_job_unknown_id_refuses(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    st = state_mod.State.load(ws / "state.json")
    with pytest.raises(CheckError, match="no job"):
        st.update_job("999")


# ---------------------------------------------------------------- holdout

def test_record_holdout_hashes_the_directory(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    (ws / "holdout" / "t1.py").write_text("assert True\n", encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")
    rec = st.record_holdout("tb-writer")
    assert rec["written_by"] == "tb-writer"
    assert rec["sha"].startswith("dir_text:")
    st.save()
    assert json.loads((ws / "state.json").read_text())["holdout"]["sha"] == rec["sha"]


# -------------------------------------------------------------- toolchain

def test_set_toolchain(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    st = state_mod.State.load(ws / "state.json")
    rec = st.set_toolchain("/tools/iic-osic-2026.09", "abc123", "gf180mcuD",
                           "def456")
    assert rec["pdk"] == "gf180mcuD"
    assert st.data["toolchain"]["image"] == "/tools/iic-osic-2026.09"


# ----------------------------------------------------------------- budget

def test_budget_lazily_defaults_and_consumes(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    st = state_mod.State.load(ws / "state.json")
    assert st.budget("fix_loops.lint") == 3
    assert st.budget("fix_loops.lint", consume=True) == 2
    assert st.budget("fix_loops.lint", consume=True) == 1
    st.budget("fix_loops.lint", consume=True)
    with pytest.raises(CheckError, match="exhausted"):
        st.budget("fix_loops.lint", consume=True)


# ------------------------------------------------------------------ human

def test_human_checkpoint_accepts_h_prefixed_ids(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    st = state_mod.State.load(ws / "state.json")
    approve(st, "H1")
    assert st.data["human"]["H1"]["status"] == "approved"
    with pytest.raises(CheckError, match="H<n>"):
        st.present_checkpoint("checkpoint-2")
    with pytest.raises(CheckError, match="H<n>"):
        st.record_human("checkpoint-2", "approved", "yes")


def test_human_approval_needs_an_answer_given_at_the_presentation(tmp_path):
    """ece298a round 2: a worker recorded ade H1 approved from a note in its
    brief. No wording of a note written before the presentation can quote
    the challenge the presentation makes up."""
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "dac", phase="P4")
    st = state_mod.State.load(ws / "state.json")
    brief = "H1 approved in advance by the owner; proceed to layout"
    with pytest.raises(CheckError, match="no open presentation"):
        st.record_human("H1", "approved", brief)
    chal = st.present_checkpoint("H1")["challenge"]
    assert re.fullmatch(r"H1-[0-9a-f]{6}", chal)
    for worded in (brief, "approved H1", "H1-approve", chal + "0"):
        with pytest.raises(CheckError, match="does not quote"):
            st.record_human("H1", "approved", worded)
    assert st.data["human"]["H1"]["status"] == "presented"
    rec = st.record_human("H1", "approved", f"ok, {chal.upper()} approve")
    assert rec["status"] == "approved" and chal.upper() in rec["answer"]
    # one answer per presentation: the same reply cannot be recorded again
    with pytest.raises(CheckError, match="last verdict: approved"):
        st.record_human("H1", "approved", f"{chal} approve")
    # a new presentation makes a new challenge; the old reply does not fit
    chal2 = st.present_checkpoint("H1")["challenge"]
    assert chal2 != chal
    with pytest.raises(CheckError, match="does not quote"):
        st.record_human("H1", "approved", f"{chal} approve")


def test_human_approval_refuses_when_the_workspace_moved_since(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8", phase="P4")
    st = state_mod.State.load(ws / "state.json")
    chal = st.present_checkpoint("H1")["challenge"]
    st.record_gate("lint", {"status": "pass"})
    with pytest.raises(CheckError, match="changed since H1 was presented"):
        st.record_human("H1", "approved", f"{chal} approve")
    (ws / "log" / "P4-digest.md").write_text("- numbers\n", encoding="utf-8")
    chal = st.present_checkpoint("H1")["challenge"]
    (ws / "log" / "P4-digest.md").write_text("- other\n", encoding="utf-8")
    with pytest.raises(CheckError, match="changed since H1 was presented"):
        st.record_human("H1", "approved", f"{chal} approve")
    chal = st.present_checkpoint("H1")["challenge"]
    assert st.record_human("H1", "approved", chal)["status"] == "approved"


@pytest.mark.parametrize("skill,leave,cp", [
    ("vde", "P4", "H1"), ("vde", "P8", "H2"), ("ade", "P4", "H1"),
    ("ade", "P5", "H2"), ("msde", "P4", "H2")])
def test_set_phase_refuses_past_an_unapproved_checkpoint(
        tmp_path, skill, leave, cp):
    """Breakage 13: set-phase never checked H1. --force waives gate
    evidence, never the person's checkpoint; a rejection holds too."""
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, skill, "blk", phase=leave)
    st = state_mod.State.load(ws / "state.json")
    nxt = state_mod.PHASES[state_mod.PHASES.index(leave) + 1]
    with pytest.raises(CheckError, match=f"checkpoint {cp} not approved"):
        st.set_phase(nxt, require_gates=False)
    chal = st.present_checkpoint(cp)["challenge"]
    st.record_human(cp, "rejected", f"{chal} reject: tighten the margin")
    with pytest.raises(CheckError, match=f"checkpoint {cp} not approved"):
        st.set_phase(nxt, require_gates=False)
    assert st.data["phase"] == leave
    approve(st, cp)
    st.set_phase(nxt, require_gates=False)
    assert st.data["phase"] == nxt
    # a phase no checkpoint closes moves without one
    ws2 = tmp_path / "other"
    shutil.copytree(FIXTURES / "ws-empty", ws2)
    state_mod.State.init(ws2, skill, "b", phase="P2")
    st2 = state_mod.State.load(ws2 / "state.json")
    st2.set_phase("P3", require_gates=False)
    assert st2.data["phase"] == "P3"


def test_set_phase_refuses_a_recorded_fail_unless_forced(tmp_path):
    """Breakage 13: a recorded FAIL passed with only a warning."""
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8", phase="P1")
    st = state_mod.State.load(ws / "state.json")
    st.record_gate("spec_lint", {"status": "fail"})
    with pytest.raises(CheckError, match="spec_lint recorded FAIL"):
        st.set_phase("P2")
    assert st.data["phase"] == "P1"
    warnings = st.set_phase("P2", require_gates=False)
    assert any("recorded FAIL (--force)" in w["msg"] for w in warnings)
    forced = [h for h in st.data["history"] if h["event"] == "phase_forced"]
    assert forced and forced[-1]["failed"] == ["spec_lint"]
    # the kept case: a recorded pass advances with no force and no record
    st.record_gate("spec_lint", {"status": "pass"})
    st.set_phase("P3")
    assert [h for h in st.data["history"]
            if h["event"] == "phase_forced"] == forced


def test_cli_present_then_human_quotes_the_challenge(tmp_path, capsys):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8", phase="P4")
    base = ["--workspace", str(ws)]
    assert state_mod.main(["present", "--checkpoint", "H1", *base]) == 0
    chal = json.loads(capsys.readouterr().out)["challenge"]
    assert state_mod.main(["human", "--checkpoint", "H1", "--status",
                           "approved", "--answer", "approved", *base]) == 2
    assert "does not quote" in json.loads(capsys.readouterr().out)["error"]
    assert state_mod.main(["human", "--checkpoint", "H1", "--status",
                           "approved", "--answer", f"{chal} yes", *base]) == 0
    capsys.readouterr()
    st = state_mod.State.load(ws / "state.json")
    assert st.data["human"]["H1"]["status"] == "approved"


# ------------------------------------------------- scope-out from the H1 answer

MIM_RULING = ("MIM capacitor spread is out of scope for this rung: MIM "
              "stays pinned typical, a known limit.")


def test_scope_out_is_taken_only_from_the_recorded_h1_answer(tmp_path,
                                                             capsys):
    """A person ruled MIM spread out of scope in the note recorded with
    their H1 approval. state.py carries that ruling onto the H1 record, and
    refuses a scope-out whose dimension the recorded answer does not name,
    whose words are not the person's, or which does not rule anything out."""
    import corners as corners_mod
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "ring_osc_div", phase="P4")
    base = ["--workspace", str(ws)]
    st = state_mod.State.load(ws / "state.json")
    # no approved H1 yet: nothing to take a scope-out from
    with pytest.raises(CheckError, match="H1 is not approved"):
        st.record_scope_out("mim_cap", MIM_RULING)
    chal = st.present_checkpoint("H1")["challenge"]
    st.record_human("H1", "approved", f"approved {chal}", MIM_RULING)
    st.save()
    refused = [
        # the answer never names the resistor
        ("resistor", MIM_RULING, "does not name resistor"),
        ("resistor", "resistor spread is out of scope", "not in the recorded"),
        # words the person did not write
        ("mim_cap", "MIM spread is out of scope", "not in the recorded"),
        # the person's words, but no ruling in them
        ("mim_cap", "MIM capacitor spread", "does not rule it out"),
        ("vth", MIM_RULING, "unknown dimension"),
        ("mim_cap", "   ", "--quote is empty"),
    ]
    for dim, quote, why in refused:
        assert state_mod.main(["scope-out", "--dimension", dim,
                               "--quote", quote, *base]) == 2, (dim, quote)
        out = json.loads(capsys.readouterr().out)
        assert why in out["error"], (dim, quote, out)
    st = state_mod.State.load(ws / "state.json")
    assert "scope_out" not in st.data["human"]["H1"]
    # the person's own sentence, whitespace and case aside
    assert state_mod.main(["scope-out", "--dimension", "mim_cap", "--quote",
                           "mim capacitor spread is  out of scope", *base]) == 0
    capsys.readouterr()
    st = state_mod.State.load(ws / "state.json")
    [rec] = st.data["human"]["H1"]["scope_out"]
    assert rec["dimension"] == "mim_cap" and rec["pinned"] == "typical"
    assert [s["dimension"] for s in corners_mod.recorded_scope_outs(st.data)] \
        == ["mim_cap"]
    # a hand edit that widens it no longer verifies, and nothing reads it
    rec["dimension"] = "resistor"
    with pytest.raises(CheckError, match="does not verify"):
        corners_mod.recorded_scope_outs(st.data)
    # a new presentation of H1 drops it with the answer it came from
    rec["dimension"] = "mim_cap"
    st.present_checkpoint("H1")
    assert corners_mod.recorded_scope_outs(st.data) == []


@pytest.mark.parametrize("note, dim, quote, kept", [
    # a negated ruling rules nothing out
    ("MIM capacitor spread is not out of scope.", "mim_cap",
     "MIM capacitor spread is not out of scope", None),
    ("MIM spread should never be pinned typical.", "mim_cap",
     "MIM spread should never be pinned typical", None),
    # a ruling on the other device, quoted whole, rules out only that one
    ("Resistor spread must be swept; MIM capacitor spread is out of scope.",
     "resistor",
     "Resistor spread must be swept; MIM capacitor spread is out of scope",
     ("mim_cap", "MIM capacitor spread is out of scope")),
    ("MIM spread is out of scope but resistor spread is swept.", "resistor",
     "MIM spread is out of scope but resistor spread is swept",
     ("mim_cap", "MIM spread is out of scope")),
    # one clause that rules on both devices is not a ruling on either alone
    ("MIM out of scope, resistors swept.", "resistor",
     "MIM out of scope, resistors swept", None),
])
def test_scope_out_refuses_a_ruling_the_person_did_not_make(
        tmp_path, capsys, note, dim, quote, kept):
    """The quote is verbatim in the recorded H1 note, names the dimension
    and holds an out-of-scope phrase, but the ruling it makes is negated or
    is about the other device: refused. Where the note does rule the other
    device out, that ruling is still taken."""
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "ring_osc_div", phase="P4")
    st = state_mod.State.load(ws / "state.json")
    chal = st.present_checkpoint("H1")["challenge"]
    st.record_human("H1", "approved", f"approved {chal}", note)
    st.save()
    base = ["--workspace", str(ws)]
    assert state_mod.main(["scope-out", "--dimension", dim, "--quote", quote,
                           *base]) == 2
    out = json.loads(capsys.readouterr().out)
    assert "refused" in out["error"], out
    st = state_mod.State.load(ws / "state.json")
    assert "scope_out" not in st.data["human"]["H1"]
    if kept:
        kdim, kquote = kept
        assert state_mod.main(["scope-out", "--dimension", kdim, "--quote",
                               kquote, *base]) == 0
        capsys.readouterr()
        st = state_mod.State.load(ws / "state.json")
        assert [s["dimension"] for s in st.data["human"]["H1"]["scope_out"]] \
            == [kdim]


# ------------------------------------------------------------- edit class

def test_edit_unknown_class_for_skill_refuses(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    st = state_mod.State.load(ws / "state.json")
    with pytest.raises(CheckError, match="unknown edit class"):
        st.apply_edit("netlist_edit")   # an /ade class, not /vde's


def test_apply_edit_analog_skill_uses_ade_classes(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "mirror")
    st = state_mod.State.load(ws / "state.json")
    rec = st.apply_edit("sizing_edit")
    assert set(rec["gates"]) == {"sim_tt", "sim_pvt", "bench_strength", "mc",
                                 "lvs", "pex_sim", "release"}


# --------------------------------------------------------------- snapshot

def test_snapshot_and_restore_roundtrip(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    (ws / "rtl" / "counter8.v").write_text("v1\n", encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")
    st.snapshot("pre-fix", files=["rtl/counter8.v"])
    st.save()

    (ws / "rtl" / "counter8.v").write_text("v2 - broken\n", encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")
    out = st.restore("pre-fix")
    st.save()
    assert out["restored"] == ["rtl/counter8.v"]
    assert (ws / "rtl" / "counter8.v").read_text(encoding="utf-8") == "v1\n"


def test_snapshot_resolves_the_workspace_from_state_json_not_cwd(
        tmp_path, monkeypatch):
    """The spi_fifo /vde run: a workspace init'd by a relative path, then
    snapshotted from a different cwd, wrote an EMPTY snapshot into a stray
    tree under that cwd. The snapshot must land in the workspace and hold
    the files, wherever it is run from."""
    root = tmp_path / "run"
    (root / "blocks").mkdir(parents=True)
    monkeypatch.chdir(root)
    state_mod.State.init(Path("blocks/b"), "vde", "counter8")
    (root / "blocks/b/rtl/counter8.v").write_text("v1\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    st = state_mod.State.load(root / "blocks/b/state.json")
    out = st.snapshot("pre-fix", files=["rtl/counter8.v"])
    st.save()
    assert [f["path"] for f in out["files"]] == ["rtl/counter8.v"]
    assert (root / "blocks/b/state_snapshots/pre-fix/rtl/counter8.v").is_file()
    assert not (elsewhere / "blocks").exists()


def test_snapshot_refuses_state_json_itself(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    st = state_mod.State.load(ws / "state.json")
    with pytest.raises(safelib.ContainmentError):
        st.snapshot("bad", files=["state.json"])


def test_snapshot_default_expands_directory_artifacts_into_files(tmp_path):
    """M5, found running the fix loop for real: a directory-kind artifact
    (rtl/, tb/, formal/, holdout/, ...) used to protect NOTHING under a
    bare `snapshot` with no explicit --files, because _default_snapshot_rels
    only ever checked is_file() - true for zero of /vde's own design
    artifacts. A default snapshot must expand every registered directory
    artifact to the files it actually contains, and skip a bytecode cache
    the design never wrote."""
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    (ws / "rtl" / "counter8.v").write_text("v1\n", encoding="utf-8")
    (ws / "rtl" / "sub").mkdir()
    (ws / "rtl" / "sub" / "helper.v").write_text("h1\n", encoding="utf-8")
    pycache = ws / "rtl" / "__pycache__"
    pycache.mkdir()
    (pycache / "x.pyc").write_bytes(b"junk")

    st = state_mod.State.load(ws / "state.json")
    st.set_artifact("rtl", "rtl")
    st.save()

    st = state_mod.State.load(ws / "state.json")
    out = st.snapshot("pre-fix-dir")
    st.save()

    paths = {f["path"] for f in out["files"]}
    assert "rtl/counter8.v" in paths
    assert "rtl/sub/helper.v" in paths
    assert not any("__pycache__" in p or p.endswith(".pyc") for p in paths)

    (ws / "rtl" / "counter8.v").write_text("v2 - broken\n", encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")
    restored = st.restore("pre-fix-dir")
    st.save()
    assert "rtl/counter8.v" in restored["restored"]
    assert (ws / "rtl" / "counter8.v").read_text(encoding="utf-8") == "v1\n"


# ------------------------------------------------------------------ issues

def test_issue_waived_requires_note_and_approved_by(tmp_path):
    """A waiver that closes an issue with no recorded reason and no
    recorded approver is exactly the silent-waive the fix loop's own rule
    (no path may skip/waive/close a gate or issue without a recorded,
    approved reason) refuses."""
    ws = ws_empty(tmp_path)
    st = state_mod.State.init(ws, "vde", "counter8")
    rec = st.open_issue({"gate": "mutate", "fixer": "testbench"})
    with pytest.raises(CheckError, match="note.*approved-by"):
        st.update_issue(rec["id"], status="waived")
    with pytest.raises(CheckError, match="note.*approved-by"):
        st.update_issue(rec["id"], status="waived", note="reason only")
    with pytest.raises(CheckError, match="note.*approved-by"):
        st.update_issue(rec["id"], status="waived", approved_by="alice")
    waived = st.update_issue(rec["id"], status="waived",
                             note="proven equivalent mutant",
                             approved_by="alice")
    assert waived["status"] == "waived"
    assert waived["note"] == "proven equivalent mutant"
    assert waived["approved_by"] == "alice"
    assert waived["closed"]


def test_issue_cli_waive_requires_both_flags(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    st = state_mod.State.load(ws / "state.json")
    rec = st.open_issue({"gate": "mutate", "fixer": "testbench"})
    st.save()
    code = state_mod.main(["issue", "--workspace", str(ws), "--id",
                          str(rec["id"]), "--status", "waived"])
    assert code == 2


# ----------------------------------------------------------------- CLI

def test_cli_main_json_error_contract(tmp_path, capsys):
    code = state_mod.main(["show", "--workspace", str(tmp_path / "nope")])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert "remediation" in out


# ------------------------------------------- holdout drift (breakage 12)

def _pinned_holdout(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    (ws / "holdout" / "t1.py").write_text("assert True\n", encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")
    st.record_holdout("tb-writer")
    return ws, st


def test_holdout_unchanged_is_not_drift(tmp_path):
    ws, st = _pinned_holdout(tmp_path)
    assert st.holdout_drift() is None
    st.rehash()                                    # a bare rehash still runs
    assert st.resume_summary()["holdout_drift"] is None


def test_undeclared_holdout_change_refuses_rehash_repin_and_gate(tmp_path):
    ws, st = _pinned_holdout(tmp_path)
    (ws / "holdout" / "t1.py").write_text("assert 1\n", encoding="utf-8")
    drift = st.holdout_drift()
    assert drift["declared"] is None and drift["current"] != drift["pinned"]
    assert st.resume_summary()["holdout_drift"]["declared"] is None
    for call in (lambda: st.rehash(), lambda: st.rehash(["holdout"]),
                 lambda: st.record_holdout("tb-writer"),
                 lambda: st.record_gate("holdout", {"status": "pass"})):
        with pytest.raises(CheckError, match="holdout_edit"):
            call()
    # a rehash that names other artifacts only is not the holdout's business
    st.rehash(["rtl"])


def test_holdout_edit_declares_the_change_and_repin_consumes_it(tmp_path):
    ws, st = _pinned_holdout(tmp_path)
    old = st.data["holdout"]["sha"]
    (ws / "holdout" / "t1.py").write_text("assert 1\n", encoding="utf-8")
    st.apply_edit("holdout_edit", note="fix a wrong expected value")
    assert st.holdout_drift()["declared"]["class"] == "holdout_edit"
    st.rehash()
    st.record_gate("holdout", {"status": "pass"})
    rec = st.record_holdout("tb-writer")
    assert rec["replaced_sha"] == old and rec["sha"] != old
    assert st.holdout_drift() is None
    # the declaration was spent by the re-pin: a further change needs its own
    (ws / "holdout" / "t1.py").write_text("assert 2\n", encoding="utf-8")
    with pytest.raises(CheckError, match="no edit declares it"):
        st.rehash()


def test_spec_edit_also_declares_a_holdout_change(tmp_path):
    ws, st = _pinned_holdout(tmp_path)
    (ws / "holdout" / "t1.py").write_text("assert 1\n", encoding="utf-8")
    st.apply_edit("spec_edit")
    assert st.holdout_drift()["declared"]["class"] == "spec_edit"
    st.record_holdout("tb-writer")


def test_edit_declared_before_the_pin_does_not_cover_a_later_change(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    (ws / "holdout" / "t1.py").write_text("assert True\n", encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")
    st.apply_edit("holdout_edit")
    st.record_holdout("tb-writer")
    (ws / "holdout" / "t1.py").write_text("assert 1\n", encoding="utf-8")
    assert st.holdout_drift()["declared"] is None
    with pytest.raises(CheckError):
        st.rehash()


def test_init_of_a_nested_msde_side_refuses_a_name_the_split_did_not_give(
        tmp_path):
    parent = tmp_path / "blocks" / "r2r_dac"
    state_mod.State.init(parent, "msde", "r2r_dac")
    with pytest.raises(CheckError, match="r2r_dac_analog"):
        state_mod.State.init(parent / "analog", "ade", "analog")
    assert not (parent / "analog" / "state.json").exists()
    st = state_mod.State.init(parent / "analog", "ade", "r2r_dac_analog")
    assert st.data["block"] == "r2r_dac_analog"
    state_mod.State.init(parent / "digital", "vde", "r2r_dac")
    # outside an msde workspace a directory called analog is just a name
    state_mod.State.init(tmp_path / "analog", "ade", "analog")


# ---- holdout_stimulus_edit: a held-out stimulus fix is declared only when
# check_holdout_edit.py proves nothing that judges moved.

STIM_BASE = """\
import cocotb
from helpers import setup

# req: REQ-1
@cocotb.test()
async def test_a(dut):
    fb, rises = await setup(dut)
    assert len(rises) >= 2, "stalled"
"""


def _stimulus_ws(tmp_path):
    ws, st = _pinned_holdout(tmp_path)
    (ws / "holdout" / "t1.py").write_text(STIM_BASE, encoding="utf-8")
    st.apply_edit("holdout_edit")
    st.record_holdout("tb-writer")
    st.snapshot("pre-fix-holdout-a1", ["holdout/t1.py"])
    return ws, st


def test_stimulus_edit_that_only_moves_stimulus_is_declared(tmp_path):
    ws, st = _stimulus_ws(tmp_path)
    (ws / "holdout" / "t1.py").write_text(
        STIM_BASE.replace("fb, rises =", "fb, rises, _ ="), encoding="utf-8")
    rec = st.apply_edit("holdout_stimulus_edit",
                        baseline="pre-fix-holdout-a1")
    assert rec["human_hold"] == 1 and rec["baseline"] == "pre-fix-holdout-a1"
    assert st.holdout_drift()["declared"]["class"] == "holdout_stimulus_edit"


def test_stimulus_edit_that_loosens_an_assert_is_refused(tmp_path):
    ws, st = _stimulus_ws(tmp_path)
    (ws / "holdout" / "t1.py").write_text(
        STIM_BASE.replace("fb, rises =", "fb, rises, _ =")
        .replace(">= 2", ">= 1"), encoding="utf-8")
    with pytest.raises(CheckError, match="holdout_judgement_changed"):
        st.apply_edit("holdout_stimulus_edit", baseline="pre-fix-holdout-a1")
    assert st.holdout_drift()["declared"] is None
    # and it cannot be declared without a baseline at all
    with pytest.raises(CheckError, match="--baseline"):
        st.apply_edit("holdout_stimulus_edit")
    with pytest.raises(CheckError, match="holdout_stimulus_edit only"):
        st.apply_edit("tb_edit", baseline="pre-fix-holdout-a1")
