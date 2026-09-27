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
    assert len(problems) == len(attest_mod.applicable_gates("msde"))
    assert not (ws / "reports" / "checks.json").exists()


def test_build_succeeds_when_every_gate_is_fresh_pass(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    att, problems = attest_mod.build(ws)
    assert problems == []
    assert att["skill"] == "msde"
    assert len(att["checks"]) == len(attest_mod.applicable_gates("msde"))
    path = attest_mod.write_attestation(ws, att)
    assert path.is_file()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["attestation_sha256"] == att["attestation_sha256"]


def pass_every_gate_but_release(ws: Path, skill: str) -> None:
    st = state_mod.State.load(ws / "state.json")
    for g in statelib.load_map()["gate_inputs"][skill]:
        if g != "release":
            st.record_gate(g, {"status": "pass"})
    st.save()


def test_release_is_not_among_the_gates_it_owes():
    for skill in ("vde", "ade", "msde"):
        assert "release" in statelib.load_map()["gate_inputs"][skill]
        assert "release" not in attest_mod.applicable_gates(skill)


def test_first_release_passes_without_an_earlier_release(tmp_path):
    """release calls build(); if build() owed release too, the first release
    could never pass."""
    ws = make_ws(tmp_path)
    pass_every_gate_but_release(ws, "msde")
    att, problems = attest_mod.build(ws)
    assert problems == []
    assert att is not None
    assert "release" not in [c["gate"] for c in att["checks"]]


def test_release_still_refuses_a_stale_sibling(tmp_path):
    """Leaving release out must not loosen it: a sibling gate gone stale
    still blocks, even with an earlier release pass on record."""
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    st = state_mod.State.load(ws / "state.json")
    st.apply_edit("interface_edit")
    st.save()
    att, problems = attest_mod.build(ws)
    assert att is None
    assert any("stale" in p for p in problems)
    assert not any(p.startswith("release:") for p in problems)


def test_build_refuses_with_an_open_issue_even_if_gates_pass(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    st = state_mod.State.load(ws / "state.json")
    st.open_issue({"gate": "cosim", "fixer": "review"})
    st.save()
    att, problems = attest_mod.build(ws)
    assert att is None
    assert any("open issue" in p for p in problems)


def test_build_treats_an_underdocumented_waiver_as_still_open(tmp_path):
    """state.py's own CLI refuses `issue --status waived` without --note
    and --approved-by (tests/test_state.py), but attest.py must not trust
    the status LABEL alone either - a hand-edited state.json (or a waiver
    written before that requirement landed) with `status: waived` and
    neither field must still block release, exactly like an open issue."""
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    st = state_mod.State.load(ws / "state.json")
    rec = st.open_issue({"gate": "cosim", "fixer": "review"})
    rec["status"] = "waived"          # bypasses update_issue's own guard
    st.save()
    att, problems = attest_mod.build(ws)
    assert att is None
    assert any("open issue" in p for p in problems)


def test_build_accepts_a_properly_documented_waiver(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    st = state_mod.State.load(ws / "state.json")
    rec = st.open_issue({"gate": "cosim", "fixer": "review"})
    st.update_issue(rec["id"], status="waived",
                    note="proven equivalent mutant", approved_by="alice")
    st.save()
    att, problems = attest_mod.build(ws)
    assert problems == []
    assert att is not None


def test_build_refuses_with_an_escalated_issue(tmp_path):
    """"escalated" is the fix loop's own outcome for a finding a human has
    to decide on - never resolved by build() alongside "open"/"fixing"
    (M5, found running the fix loop for real)."""
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    st = state_mod.State.load(ws / "state.json")
    rec = st.open_issue({"gate": "cosim", "fixer": "review"})
    st.update_issue(rec["id"], status="escalated")
    st.save()
    att, problems = attest_mod.build(ws)
    assert att is None
    assert any("open issue" in p for p in problems)


def test_build_counts_superseded_closed_only_while_its_replacement_is_fixed(tmp_path):
    ws = make_ws(tmp_path)
    pass_every_gate(ws, "msde")
    st = state_mod.State.load(ws / "state.json")
    old = st.open_issue({"gate": "cosim", "fixer": "review"})
    new = st.open_issue({"gate": "cosim", "fixer": "review"})
    st.update_issue(new["id"], status="fixed")
    st.update_issue(old["id"], status="superseded", by=new["id"])
    st.save()
    att, problems = attest_mod.build(ws)
    assert problems == [] and att is not None

    st = state_mod.State.load(ws / "state.json")
    st.update_issue(new["id"], status="escalated")
    st.save()
    att, problems = attest_mod.build(ws)
    assert att is None
    assert any("2 open issue" in p for p in problems)


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


# ------------------------------------------------- a scope-out made at H1

MIM_RULING = ("MIM capacitor spread is out of scope for this rung: MIM "
              "stays pinned typical, a known limit.")


def scope_out_mim_at_h1(ws: Path) -> None:
    st = state_mod.State.load(ws / "state.json")
    chal = st.present_checkpoint("H1")["challenge"]
    st.record_human("H1", "approved", f"approved {chal}", MIM_RULING)
    st.record_scope_out("mim_cap", "MIM capacitor spread is out of scope")
    st.save()


def test_the_release_record_carries_a_scope_out_made_at_h1(tmp_path):
    ws = make_ws(tmp_path, skill="ade", block="ring_osc_div")
    pass_every_gate(ws, "ade")
    att, _ = attest_mod.build(ws)
    assert "scoped_out" not in att
    attest_mod.write_attestation(ws, att)
    scope_out_mim_at_h1(ws)
    # the record written before the ruling no longer stands
    v = attest_mod.verify(ws)
    assert v["valid"] is False and "scope-outs" in v["reason"]
    att, problems = attest_mod.build(ws)
    assert problems == []
    [so] = att["scoped_out"]
    assert so["dimension"] == "mim_cap" and so["pinned"] == "typical"
    assert so["quote"] == "MIM capacitor spread is out of scope"
    attest_mod.write_attestation(ws, att)
    assert attest_mod.verify(ws)["valid"] is True
    # one that no longer verifies against the answer refuses the release
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    data["human"]["H1"]["scope_out"][0]["quote"] = "resistors out of scope"
    (ws / "state.json").write_text(json.dumps(data), encoding="utf-8")
    att, problems = attest_mod.build(ws)
    assert att is None and any("does not verify" in p for p in problems)
