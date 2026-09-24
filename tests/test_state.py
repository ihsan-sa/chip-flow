"""engine/scripts/state.py: state.json v3 - schema, freshness, gate
recording, edit classes, jobs, holdout, snapshots. Hermetic.

The M1 done criterion (docs/design.md, "### M1."): tests/fixtures/ws-empty
goes through state.py init, edit --class rtl_edit, freshness and resume
with the marks section 1.6 lists.
"""
from __future__ import annotations

import json
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


# --------------------------------------------------------- gate recording

def test_record_gate_requires_pass_or_fail(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    st = state_mod.State.load(ws / "state.json")
    with pytest.raises(CheckError, match="pass\\|fail"):
        st.record_gate("lint", {"status": "bogus"})


def test_set_phase_refuses_past_a_gate_with_no_result(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8", phase="P4")
    st = state_mod.State.load(ws / "state.json")
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
    st.record_human("H1", "approved", note="looks fine")
    assert st.data["human"]["H1"]["status"] == "approved"
    with pytest.raises(CheckError, match="H<n>"):
        st.record_human("checkpoint-2", "approved")


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
