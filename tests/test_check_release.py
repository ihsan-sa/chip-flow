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
    data = checklib.load_json(ws / "state.json", "state.json")
    inputs_sha = attest_mod.waiver_inputs_sha256(data["gates"]["mutate"])
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
    data = checklib.load_json(ws / "state.json", "state.json")
    inputs_sha = attest_mod.waiver_inputs_sha256(data["gates"]["mutate"])
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
    data = checklib.load_json(ws / "state.json", "state.json")
    inputs_sha = attest_mod.waiver_inputs_sha256(data["gates"]["mutate"])
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
