"""engine/scripts/check_release.py + engine/scripts/attest.py's waiver
mechanism: the release gate (docs/design.md 1.5's `release` row, "### M3.").
Hermetic - state.py/statelib only, gate results are fabricated with
record_gate (as test_attest.py's own pass_every_gate does), never a real
tool run."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_release  # noqa: E402
import attest as attest_mod  # noqa: E402
import checklib  # noqa: E402
import state as state_mod  # noqa: E402
import statelib  # noqa: E402

RTL_A = ("module top(input wire clk, output reg q);\n"
        "  always @(posedge clk) q <= ~q;\nendmodule\n")
RTL_B = ("module top(input wire clk, output reg q);\n"
        "  always @(posedge clk) q <= q;\nendmodule\n")


def make_vde_ws(tmp_path: Path, block: str = "counter8") -> Path:
    ws = tmp_path / "ws"
    state_mod.State.init(ws, "vde", block)
    (ws / "rtl" / "top.v").write_text(RTL_A, encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(
        "top: top\nrequirements: []\n", encoding="utf-8")
    return ws


def pass_every_gate(ws: Path, *, fail: str | None = None) -> None:
    st = state_mod.State.load(ws / "state.json")
    for g in statelib.load_map()["gate_inputs"]["vde"]:
        st.record_gate(g, {"status": "fail" if g == fail else "pass"})
    st.save()


def write_waivers(ws: Path, waivers: list[dict]) -> None:
    (ws / "reports").mkdir(exist_ok=True)
    (ws / "reports" / "waivers.json").write_text(
        json.dumps(waivers), encoding="utf-8")


def current_inputs_sha(ws: Path, gate: str) -> str | None:
    """The waiver durability hash bound to `gate`'s CURRENT inputs (never
    its last recorded ones) - what a real approval would capture."""
    data = checklib.load_json(ws / "state.json", "state.json")
    fresh = statelib.freshness_report(data, ws)
    return attest_mod.waiver_inputs_sha256(fresh["gates"][gate]["current_inputs"])


def test_release_refuses_on_a_fresh_workspace(tmp_path, capsys):
    ws = make_vde_ws(tmp_path)
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["status"] == "violations"
    assert out["violations"]
    assert not (ws / "reports" / "checks.json").exists()


def test_release_passes_once_every_gate_is_fresh(tmp_path, capsys):
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws)
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert (ws / "reports" / "checks.json").is_file()
    assert out["waived"] == []


def test_release_records_tool_and_version_per_gate(tmp_path, capsys):
    # docs/design.md section 2: "The record of what ran is ... gate, tool
    # and version, inputs and hashes, result, timestamp."
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws)
    code = check_release.main(["--workspace", str(ws)])
    assert code == 0, json.loads(capsys.readouterr().out)
    checks_doc = json.loads((ws / "reports" / "checks.json").read_text())
    by_gate = {c["gate"]: c for c in checks_doc["checks"]}
    assert by_gate["formal"]["tool"] == "formal"     # a real, built gate
    assert by_gate["harden"]["tool"] == "harden"      # M4: built, no longer a stub
    assert "release" not in by_gate    # release is what decides, not a check
    assert all(c["version"] == checklib.CHECKER_VERSION
              for c in checks_doc["checks"])


def test_first_release_passes_with_no_earlier_release(tmp_path, capsys):
    # release calls attest's build(); a release that owed itself could never
    # pass the first time.
    ws = make_vde_ws(tmp_path)
    st = state_mod.State.load(ws / "state.json")
    for g in statelib.load_map()["gate_inputs"]["vde"]:
        if g != "release":
            st.record_gate(g, {"status": "pass"})
    st.save()
    code = check_release.main(["--workspace", str(ws)])
    assert code == 0, json.loads(capsys.readouterr().out)


def test_release_refuses_after_rtl_edit_then_passes_once_regated(tmp_path, capsys):
    # M3's own done criterion (docs/design.md "### M3."): "release refuses a
    # workspace whose RTL changed after sim passed and passes once the
    # gates re-run."
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws)
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out

    (ws / "rtl" / "top.v").write_text(RTL_B, encoding="utf-8")
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert any("stale" in v["msg"] for v in out["violations"]), out

    pass_every_gate(ws)  # "the gates re-run"
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"


def test_durable_waiver_covers_a_failing_gate(tmp_path, capsys):
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws, fail="mutate")
    inputs_sha = current_inputs_sha(ws, "mutate")
    write_waivers(ws, [{
        "gate": "mutate",
        "reason": "kill rate below floor on a tiny design; tracked in a "
                 "follow-up, not a testbench defect",
        "approved_by": "a reviewer",
        "inputs_sha256": inputs_sha,
        "checker_version": checklib.CHECKER_VERSION,
    }])
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["waived"] == ["mutate"], out


def test_waiver_is_refused_once_the_rtl_moves_past_what_it_covered(tmp_path, capsys):
    # review finding: waiver_inputs_sha256 used to hash the gate's own LAST
    # RECORDED inputs (frozen at record time), so a waiver approved while
    # mutate failed against RTL A kept covering mutate even after the RTL
    # moved to B, as long as mutate itself was never re-recorded - "a waiver
    # outlives its input". docs/design.md's own done criterion: "mutate
    # fails on A, it's waived, the RTL moves to B, every other gate
    # re-runs, and release must refuse."
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws, fail="mutate")           # mutate fails against RTL A
    inputs_sha = current_inputs_sha(ws, "mutate")
    write_waivers(ws, [{
        "gate": "mutate", "reason": "kill rate below floor on RTL A",
        "approved_by": "a reviewer", "inputs_sha256": inputs_sha,
        "checker_version": checklib.CHECKER_VERSION,
    }])
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out                        # the waiver covers it on A
    assert out["waived"] == ["mutate"], out

    (ws / "rtl" / "top.v").write_text(RTL_B, encoding="utf-8")   # RTL -> B
    st = state_mod.State.load(ws / "state.json")
    for g in statelib.load_map()["gate_inputs"]["vde"]:
        if g == "mutate":
            continue                             # mutate itself never re-runs
        st.record_gate(g, {"status": "pass"})    # "every other gate re-runs"
    st.save()

    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert any(v.get("module") == "mutate" for v in out["violations"]), out


def test_waiver_is_refused_when_hash_valid_is_false_even_if_bound_to_current(
        tmp_path, capsys):
    # a waiver written against the gate's CURRENT inputs directly, while the
    # gate's own last recorded run is against a DIFFERENT, older input - the
    # gate has never actually run against what this waiver claims to cover.
    # Comparing hashes alone would not catch this (the waiver's hash and the
    # current hash agree); only the explicit hash_valid check does.
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws, fail="mutate")            # mutate recorded against A
    (ws / "rtl" / "top.v").write_text(RTL_B, encoding="utf-8")  # RTL -> B,
                                                   # mutate never re-run
    inputs_sha = current_inputs_sha(ws, "mutate")  # hashes CURRENT (B) directly
    write_waivers(ws, [{
        "gate": "mutate",
        "reason": "approved against a state the gate never actually ran",
        "approved_by": "x", "inputs_sha256": inputs_sha,
        "checker_version": checklib.CHECKER_VERSION,
    }])
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert any("has not actually run" in v["msg"] for v in out["violations"]), out


def test_waiver_missing_fields_is_not_durable(tmp_path, capsys):
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws, fail="mutate")
    write_waivers(ws, [{"gate": "mutate", "reason": "because"}])
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert any("not durable" in v["msg"] for v in out["violations"]), out


def test_waiver_bound_to_different_inputs_is_rejected(tmp_path, capsys):
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws, fail="mutate")
    write_waivers(ws, [{
        "gate": "mutate", "reason": "because", "approved_by": "x",
        "inputs_sha256": "0" * 64, "checker_version": checklib.CHECKER_VERSION,
    }])
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert any("different inputs" in v["msg"] for v in out["violations"]), out


def test_waiver_under_a_stale_checker_version_is_rejected(tmp_path, capsys):
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws, fail="mutate")
    inputs_sha = current_inputs_sha(ws, "mutate")
    write_waivers(ws, [{
        "gate": "mutate", "reason": "because", "approved_by": "x",
        "inputs_sha256": inputs_sha, "checker_version": checklib.CHECKER_VERSION - 1,
    }])
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert any("checker_version" in v["msg"] for v in out["violations"]), out


def test_expired_waiver_is_rejected(tmp_path, capsys):
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws, fail="mutate")
    inputs_sha = current_inputs_sha(ws, "mutate")
    write_waivers(ws, [{
        "gate": "mutate", "reason": "because", "approved_by": "x",
        "inputs_sha256": inputs_sha, "checker_version": checklib.CHECKER_VERSION,
        "expires": "2000-01-01T00:00:00+00:00",
    }])
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert any("expired" in v["msg"] for v in out["violations"]), out


def test_waiver_on_an_already_passing_gate_is_inert(tmp_path, capsys):
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws)
    write_waivers(ws, [{
        "gate": "mutate", "reason": "stale leftover", "approved_by": "x",
        "inputs_sha256": "0" * 64,
    }])
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["waived"] == [], out


def test_malformed_waivers_file_is_an_error(tmp_path, capsys):
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws)
    (ws / "reports").mkdir(exist_ok=True)
    (ws / "reports" / "waivers.json").write_text(
        json.dumps({"not": "a list"}), encoding="utf-8")
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert any("JSON list" in v["msg"] for v in out["violations"]), out


def test_checks_json_validates_against_the_schema(tmp_path, capsys):
    import jsonschema
    ws = make_vde_ws(tmp_path)
    pass_every_gate(ws)
    code = check_release.main(["--workspace", str(ws)])
    assert code == 0, json.loads(capsys.readouterr().out)
    schema = json.loads(
        (ENGINE / "reference" / "checks.schema.json").read_text())
    checks_doc = json.loads(
        (ws / "reports" / "checks.json").read_text())
    jsonschema.validate(checks_doc, schema)


# --- /ade release ---------------------------------------------------------
# The same gate, run for an /ade workspace: every ade gate (gates.yaml's
# ade block, bar release) owes a fresh recorded pass, and `mc` is owed only
# when the block's own spec.yaml asks for Monte Carlo - otherwise attest
# records it as declared not applicable (ok, not run, with the reason).

import pytest  # noqa: E402

import gate as gate_mod  # noqa: E402

ADE_SPEC = ("top: current_mirror\nsupply: {vdd: 3.3}\n"
            "devices: [xmref, xmout]\ncorners: default\nmeasures:\n"
            "  - name: iout_raw\n    bounds: {min: 18e-6, max: 32e-6}\n")
ADE_MC_ON = "mc: {enabled: true, runs: 4, yield_min: 0.75}\n"
ADE_GATES = sorted(g for g in statelib.load_map()["gate_inputs"]["ade"]
                  if g != "release")
ADE_OWED = [g for g in ADE_GATES if g != "mc"]


def make_ade_ws(tmp_path: Path, spec_extra: str = "") -> Path:
    ws = tmp_path / "ws"
    state_mod.State.init(ws, "ade", "mirror")
    (ws / "spec" / "spec.yaml").write_text(ADE_SPEC + spec_extra,
                                           encoding="utf-8")
    for sub, name, text in (
            ("netlist", "mirror.cir", ".subckt current_mirror a b\n.ends\n"),
            ("tb", "mirror_tb.cir", "* bench\n.end\n"),
            ("layout", "gen_mirror.py", "# layout generator\n")):
        (ws / sub).mkdir(parents=True, exist_ok=True)
        (ws / sub / name).write_text(text, encoding="utf-8")
    return ws


def record_ade(ws: Path, gates, *, fail: str | None = None) -> None:
    st = state_mod.State.load(ws / "state.json")
    for g in gates:
        st.record_gate(g, {"status": "fail" if g == fail else "pass"})
    st.save()


def release(ws: Path, capsys) -> tuple[int, dict]:
    code = check_release.main(["--workspace", str(ws)])
    return code, json.loads(capsys.readouterr().out)


def not_ready_gates(out: dict) -> set[str]:
    return {v["msg"].split(":", 1)[0] for v in out["violations"]
            if v["kind"] == "gate_not_ready"}


def test_ade_release_row_is_real():
    row = gate_mod.load_gates(gate_mod.DEFAULT_GATES)["ade"]["release"]
    assert row["tool"] == "release"
    assert "future_tool" not in row
    assert row["strict"] is True


def test_ade_release_passes_when_every_gate_is_fresh(tmp_path, capsys):
    import jsonschema
    ws = make_ade_ws(tmp_path)
    record_ade(ws, ADE_OWED)          # mc never recorded: spec asks no MC
    code, out = release(ws, capsys)
    assert code == 0, out
    assert out["waived"] == []
    checks_doc = json.loads((ws / "reports" / "checks.json").read_text())
    by_gate = {c["gate"]: c for c in checks_doc["checks"]}
    assert set(by_gate) == set(ADE_GATES)       # mc listed, not dropped
    mc = by_gate["mc"]
    assert mc["ok"] is True and mc["ran"] is False
    assert "not applicable" in mc["reason"]
    assert mc["inputs"]["spec_yaml"]
    assert all(by_gate[g]["ran"] and by_gate[g]["ok"] for g in ADE_OWED)
    jsonschema.validate(checks_doc, json.loads(
        (ENGINE / "reference" / "checks.schema.json").read_text()))


def test_ade_release_refuses_a_fresh_workspace(tmp_path, capsys):
    ws = make_ade_ws(tmp_path)
    code, out = release(ws, capsys)
    assert code == 1, out
    assert not_ready_gates(out) == set(ADE_OWED)
    assert not (ws / "reports" / "checks.json").exists()


@pytest.mark.parametrize("gate", ADE_OWED)
def test_ade_release_refuses_one_unrun_gate(tmp_path, capsys, gate):
    ws = make_ade_ws(tmp_path)
    record_ade(ws, [g for g in ADE_OWED if g != gate])
    code, out = release(ws, capsys)
    assert code == 1, out
    assert not_ready_gates(out) == {gate}, out


@pytest.mark.parametrize("gate", ADE_OWED)
def test_ade_release_refuses_one_failed_gate(tmp_path, capsys, gate):
    ws = make_ade_ws(tmp_path)
    record_ade(ws, ADE_OWED, fail=gate)
    code, out = release(ws, capsys)
    assert code == 1, out
    assert not_ready_gates(out) == {gate}, out
    assert any("FAIL" in v["msg"] for v in out["violations"]), out


def test_ade_release_refuses_a_stale_gate_then_passes_once_regated(
        tmp_path, capsys):
    ws = make_ade_ws(tmp_path)
    record_ade(ws, ADE_OWED)
    assert release(ws, capsys)[0] == 0
    # spec_lint is the only ade gate keyed on spec.yaml alone; the edit
    # leaves MC off, so mc stays declared not applicable.
    spec = ws / "spec" / "spec.yaml"
    spec.write_text(spec.read_text() + "notes: edited after spec_lint\n",
                    encoding="utf-8")
    code, out = release(ws, capsys)
    assert code == 1, out
    assert not_ready_gates(out) == {"spec_lint"}, out
    assert any("stale" in v["msg"] for v in out["violations"]), out
    record_ade(ws, ["spec_lint"])
    assert release(ws, capsys)[0] == 0


def test_ade_release_refuses_after_netlist_edit(tmp_path, capsys):
    ws = make_ade_ws(tmp_path)
    record_ade(ws, ADE_OWED)
    (ws / "netlist" / "mirror.cir").write_text(
        ".subckt current_mirror a b c\n.ends\n", encoding="utf-8")
    code, out = release(ws, capsys)
    assert code == 1, out
    assert not_ready_gates(out) == {"netlist_lint", "sim_tt", "sim_pvt",
                                    "bench_strength", "lvs"}, out


def test_ade_release_owes_mc_when_the_spec_asks_for_it(tmp_path, capsys):
    ws = make_ade_ws(tmp_path, ADE_MC_ON)
    record_ade(ws, ADE_OWED)
    code, out = release(ws, capsys)
    assert code == 1, out
    assert not_ready_gates(out) == {"mc"}, out
    record_ade(ws, ["mc"], fail="mc")
    code, out = release(ws, capsys)
    assert code == 1 and not_ready_gates(out) == {"mc"}, out
    record_ade(ws, ["mc"])
    code, out = release(ws, capsys)
    assert code == 0, out
    checks_doc = json.loads((ws / "reports" / "checks.json").read_text())
    mc = {c["gate"]: c for c in checks_doc["checks"]}["mc"]
    assert mc["ran"] is True and mc["status"] == "pass"


def test_ade_attestation_goes_invalid_when_the_spec_turns_mc_on(
        tmp_path, capsys):
    ws = make_ade_ws(tmp_path)
    record_ade(ws, ADE_OWED)
    assert release(ws, capsys)[0] == 0
    assert attest_mod.verify(ws)["valid"] is True
    spec = ws / "spec" / "spec.yaml"
    spec.write_text(spec.read_text() + ADE_MC_ON, encoding="utf-8")
    v = attest_mod.verify(ws)
    assert v["valid"] is False
    assert "mc" in v["reason"]


def test_ade_mc_is_owed_when_the_spec_cannot_be_read(tmp_path, capsys):
    ws = make_ade_ws(tmp_path)
    record_ade(ws, ADE_OWED)
    (ws / "spec" / "spec.yaml").write_text("mc: [not, a, mapping]\n",
                                           encoding="utf-8")
    code, out = release(ws, capsys)
    assert code == 1, out
    assert "mc" in not_ready_gates(out), out


def test_ade_release_result_records_against_the_netlist(tmp_path, capsys):
    # check_release stamps the skill's own release kinds[0] (netlist for
    # ade), so gate.py can record the release pass in state.json.
    ws = make_ade_ws(tmp_path)
    record_ade(ws, ADE_OWED)
    payload, _ = check_release.run(["--workspace", str(ws)])
    row = gate_mod.load_gates(gate_mod.DEFAULT_GATES)["ade"]["release"]
    result = gate_mod.evaluate("release", row, payload)
    assert result["status"] == "pass", result
    rec = gate_mod.record_gate_result("ade", "release", row, result, ws)
    assert rec["recorded"] is True, rec


# --- msde (M10): "both nested runs released, top gates fresh" -------------

def make_msde_ws(tmp_path: Path) -> Path:
    """An msde block whose top gates and both nested sides have all passed
    and whose nested sides each hold a written, verifying checks.json."""
    ws = tmp_path / "msde"
    state_mod.State.init(ws, "msde", "sensor_counted")
    (ws / "interface.yaml").write_text("version: 1\nsignals: []\n",
                                      encoding="utf-8")
    for side, skill, kind in (("digital", "vde", "rtl"),
                              ("analog", "ade", "netlist")):
        sub = ws / side
        state_mod.State.init(sub, skill, statelib.split_block_name(sub))
        (sub / kind / "x.txt").write_text("a\n", encoding="utf-8")
        (sub / "layout" / "x.txt").write_text("a\n", encoding="utf-8")
        st = state_mod.State.load(sub / "state.json")
        for g in statelib.load_map()["gate_inputs"][skill]:
            st.record_gate(g, {"status": "pass"})
        st.save()
        att, problems = attest_mod.build(sub)
        assert att is not None, problems
        attest_mod.write_attestation(sub, att)
    st = state_mod.State.load(ws / "state.json")
    for g in statelib.load_map()["gate_inputs"]["msde"]:
        st.record_gate(g, {"status": "pass"})
    st.save()
    return ws


def test_msde_release_passes_with_both_nested_sides_released(tmp_path, capsys):
    ws = make_msde_ws(tmp_path)
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert (ws / "reports" / "checks.json").is_file()


def test_msde_release_refuses_a_stale_nested_release(tmp_path, capsys):
    ws = make_msde_ws(tmp_path)
    (ws / "digital" / "rtl" / "x.txt").write_text("b\n", encoding="utf-8")
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {(v["module"], v["kind"]) for v in out["violations"]}
    assert ("nested_digital", "nested_not_released") in kinds
    assert not any(m == "nested_analog" for m, _ in kinds)
    assert not (ws / "reports" / "checks.json").exists()


def test_msde_release_refuses_a_missing_nested_side(tmp_path, capsys):
    ws = make_msde_ws(tmp_path)
    import shutil
    shutil.rmtree(ws / "analog")
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    # the top gates read analog/layout too, so they go stale with it; the
    # nested finding names only the missing side
    assert [v["module"] for v in out["violations"]
            if v["kind"] == "nested_not_released"] == ["nested_analog"]


def test_msde_release_refuses_a_nested_side_of_the_wrong_skill(tmp_path, capsys):
    ws = make_msde_ws(tmp_path)
    data = json.loads((ws / "analog" / "state.json").read_text())
    data["skill"] = "vde"
    (ws / "analog" / "state.json").write_text(json.dumps(data))
    code = check_release.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert any("expected 'ade'" in v["msg"] for v in out["violations"])
