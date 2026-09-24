"""engine/scripts/attest.py: the record of what ran (docs/design.md section
2). M1 scope: no releaselib/waiver machinery (that is M3's, docs/design.md
"### M3."), just gates.yaml applicability + statelib freshness -> reports/
checks.json, and a derived (never hand-set) disposition."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import attest as attest_mod  # noqa: E402
import state as state_mod  # noqa: E402
import statelib  # noqa: E402


def make_ws(tmp_path: Path, skill="msde", block="sensor_counted") -> Path:
    ws = tmp_path / "ws"
    state_mod.State.init(ws, skill, block)
    return ws


def pass_every_gate(ws: Path, skill: str) -> None:
    st = state_mod.State.load(ws / "state.json")
    for g in statelib.load_map()["gate_inputs"][skill]:
        st.record_gate(g, {"status": "pass"})
    st.save()


# ------------------------------------------------------------------- build

def test_build_refuses_on_a_fresh_workspace(tmp_path):
    ws = make_ws(tmp_path)
    att, problems = attest_mod.build(ws)
    assert att is None
    assert len(problems) == len(statelib.load_map()["gate_inputs"]["msde"])
    assert not (ws / "reports" / "checks.json").exists()


def test_build_succeeds_when_every_gate_is_fresh_pass(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    att, problems = attest_mod.build(ws)
    assert problems == []
    assert att["skill"] == "msde"
    assert len(att["checks"]) == len(statelib.load_map()["gate_inputs"]["msde"])
    path = attest_mod.write_attestation(ws, att)
    assert path.is_file()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["attestation_sha256"] == att["attestation_sha256"]


def test_build_refuses_with_an_open_issue_even_if_gates_pass(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    st = state_mod.State.load(ws / "state.json")
    st.open_issue({"gate": "cosim", "fixer": "review"})
    st.save()
    att, problems = attest_mod.build(ws)
    assert att is None
    assert any("open issue" in p for p in problems)


def test_build_refuses_when_a_gate_edit_marks_it_stale(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    st = state_mod.State.load(ws / "state.json")
    st.apply_edit("interface_edit")
    st.save()
    att, problems = attest_mod.build(ws)
    assert att is None
    assert any("stale" in p for p in problems)


# ------------------------------------------------------------------ verify

def test_verify_with_no_checks_json():
    v = attest_mod.verify(Path("/tmp/definitely-not-a-real-workspace-xyz"))
    assert v["valid"] is False


def test_verify_roundtrip_after_build(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    att, _ = attest_mod.build(ws)
    attest_mod.write_attestation(ws, att)
    v = attest_mod.verify(ws)
    assert v["valid"] is True


def test_verify_detects_hand_edited_checks_json(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    att, _ = attest_mod.build(ws)
    path = attest_mod.write_attestation(ws, att)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["checks"][0]["status"] = "fail"
    path.write_text(json.dumps(doc), encoding="utf-8")
    v = attest_mod.verify(ws)
    assert v["valid"] is False
    assert "mismatch" in v["reason"]


def test_verify_detects_a_gate_going_stale_after_the_attestation(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    att, _ = attest_mod.build(ws)
    attest_mod.write_attestation(ws, att)
    st = state_mod.State.load(ws / "state.json")
    st.apply_edit("interface_edit")
    st.save()
    v = attest_mod.verify(ws)
    assert v["valid"] is False


# -------------------------------------------------------------- disposition

def test_disposition_draft_on_fresh_workspace(tmp_path):
    ws = make_ws(tmp_path)
    assert attest_mod.disposition(ws)["disposition"] == "draft"


def test_disposition_gates_green(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    assert attest_mod.disposition(ws)["disposition"] == "gates-green"


def test_disposition_blocked_on_a_recorded_fail(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    st = state_mod.State.load(ws / "state.json")
    st.record_gate("cosim", {"status": "fail", "failing_count": 1})
    st.save()
    d = attest_mod.disposition(ws)
    assert d["disposition"] == "blocked"
    assert "cosim" in d["failing"]


def test_disposition_in_progress_when_some_gates_ran(tmp_path):
    ws = make_ws(tmp_path)
    st = state_mod.State.load(ws / "state.json")
    st.record_gate("split", {"status": "pass"})
    st.save()
    assert attest_mod.disposition(ws)["disposition"] == "in-progress"


# ------------------------------------------------------------------- CLI

def test_cli_build_exit_1_then_0(tmp_path, capsys):
    ws = make_ws(tmp_path)
    code1 = attest_mod.main(["build", "--workspace", str(ws)])
    assert code1 == 1
    capsys.readouterr()
    pass_every_gate(ws, "msde")
    code2 = attest_mod.main(["build", "--workspace", str(ws)])
    assert code2 == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pass"
    assert out["disposition"]["disposition"] == "gates-green"
