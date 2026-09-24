"""engine/scripts/check_formal.py: the formal gate (docs/design.md 1.5's
`formal` row, "### M3."). Runs REAL SymbiYosys (smtbmc yices + abc pdr)
through the eda image - not hermetic, needs the eda image, same as
test_check_lint.py's own verilator-lint smoke."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_formal  # noqa: E402
import gate  # noqa: E402
import yaml as _yaml  # noqa: E402

GATES_YAML = ENGINE / "reference" / "gates.yaml"


def _formal_gate_row():
    rows = _yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]["vde"]
    return rows["formal"]


RTL_OK = """\
module top (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
endmodule
"""

# gates.yaml's own named fault for `formal`: "a register with no reset: sim
# passes, formal fails".
RTL_NO_RESET = """\
module top (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= count + 4'd1;
endmodule
"""

FORMAL_SV = """\
module top_formal (input wire clk, input wire rst, output wire [3:0] count);
  top dut (.clk(clk), .rst(rst), .count(count));
`ifdef FORMAL
  reg past_valid = 0;
  always @(posedge clk) past_valid <= 1;
  always @(posedge clk)
    if (past_valid)
      REQ_RESET: assert (!$past(rst) || count == 4'd0);
  always @(posedge clk)
    COVER_MAX: cover (count == 4'hF);
`endif
endmodule
"""

SPEC = """\
top: top
requirements:
  - id: REQ-RESET
    text: reset proven for all time, not just what a sim test tries
    check: formal
    property: REQ_RESET
formal:
  depth: 12
"""


def make_ws(tmp_path: Path, rtl_text: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "spec").mkdir(parents=True)
    (ws / "formal").mkdir(parents=True)
    (ws / "rtl" / "top.v").write_text(rtl_text, encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(SPEC, encoding="utf-8")
    (ws / "formal" / "top_formal.sv").write_text(FORMAL_SV, encoding="utf-8")
    return ws


@pytest.mark.slow
def test_clean_design_proves_with_both_engines(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_OK)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["proven"] == ["REQ-RESET"]
    assert out["smt_status"] == "PASS"
    assert out["pdr_status"] == "PASS"
    assert out["cover_points"] == ["COVER_MAX"]
    assert out["bounded"] == [] and out["failed"] == []


@pytest.mark.slow
def test_register_with_no_reset_fails(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_NO_RESET)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "property_failed" in kinds
    assert out["failed"] == ["REQ-RESET"]
    assert out["smt_status"] == "FAIL"
    gate_result = gate.evaluate("formal", _formal_gate_row(), out)
    assert gate_result["status"] == "fail"


SPEC_PROPERTY_IS_A_COVER = """\
top: top
requirements:
  - id: REQ-RESET
    text: reset proven for all time, not just what a sim test tries
    check: formal
    property: COVER_MAX
formal:
  depth: 12
"""


@pytest.mark.slow
def test_property_naming_a_cover_point_is_refused(tmp_path, capsys):
    # a `property:` label must be the ASSERT it claims to be - COVER_MAX is
    # a real label in formal/*.sv's own model (FORMAL_SV's `cover`), so
    # `label not in smt_cases` would not catch this; without the type/
    # skipped check this sails through classify_property and comes back
    # "proven" with nothing actually proven about it.
    ws = make_ws(tmp_path, RTL_OK)
    (ws / "spec" / "spec.yaml").write_text(SPEC_PROPERTY_IS_A_COVER,
                                           encoding="utf-8")
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "COVER_MAX" in out["remediation"]


def test_sby_done_error_is_refused_for_any_task(tmp_path, capsys, monkeypatch):
    # DONE (ERROR) still matches DONE_RE (run_sby's own guard is only for a
    # launcher that never reaches DONE at all) - an ERRORed task must be
    # refused too, not silently folded into bounded/proven downstream.
    ws = make_ws(tmp_path, RTL_OK)
    fake_eda = tmp_path / "fake-eda.sh"
    fake_eda.write_text("#!/bin/sh\necho 'DONE (ERROR)'\nexit 0\n",
                        encoding="utf-8")
    fake_eda.chmod(0o755)
    monkeypatch.setattr(check_formal, "EDA_BIN", fake_eda)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "DONE (ERROR)" in out["remediation"]


def test_no_formal_requirement_is_an_error(tmp_path, capsys):
    # an empty property set is a refusal, never a vacuous pass.
    ws = make_ws(tmp_path, RTL_OK)
    (ws / "spec" / "spec.yaml").write_text(
        "top: top\nrequirements:\n  - id: REQ-X\n    text: x\n    check: sim\n",
        encoding="utf-8")
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert out["status"] == "error"
    assert "empty property set" in out["remediation"]


@pytest.mark.slow
def test_missing_property_label_is_an_error(tmp_path, capsys):
    # a 'property' spec.yaml names that formal/*.sv never defines (a typo,
    # or a `bind` that silently failed to attach - see check_formal.py's own
    # header) is refused, never silently skipped.
    ws = make_ws(tmp_path, RTL_OK)
    (ws / "spec" / "spec.yaml").write_text(
        "top: top\nrequirements:\n"
        "  - id: REQ-RESET\n    text: x\n    check: formal\n"
        "    property: NOT_A_REAL_LABEL\nformal:\n  depth: 12\n",
        encoding="utf-8")
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "NOT_A_REAL_LABEL" in out["remediation"]


def test_no_rtl_files_is_an_error(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_OK)
    for f in (ws / "rtl").glob("*.v"):
        f.unlink()
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no *" in out["remediation"]


def test_crashed_launcher_is_an_error(tmp_path, capsys, monkeypatch):
    # a failed launcher (nonzero exit, no DONE line) fails the gate - never
    # silently treated as "no findings".
    ws = make_ws(tmp_path, RTL_OK)
    bad_eda = tmp_path / "bad-eda.sh"
    bad_eda.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
    bad_eda.chmod(0o755)
    monkeypatch.setattr(check_formal, "EDA_BIN", bad_eda)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no DONE line" in out["remediation"]


def test_task_substatus_parses_basecase_and_induction():
    text = ("engine_0 (smtbmc yices) returned pass for basecase\n"
           "engine_0 (smtbmc yices) returned FAIL for induction\n")
    assert check_formal.task_substatus(text) == {
        "basecase": "pass", "induction": "FAIL"}
    assert check_formal.task_substatus("nothing here at all") == {
        "basecase": None, "induction": None}


CASE_OK = {"failed": False}
CASE_FAILED = {"failed": True}


def test_classify_property_proven():
    verdict, violation = check_formal.classify_property(
        "P", "REQ", CASE_OK, {"basecase": "pass", "induction": "pass"},
        "PASS", 20)
    assert verdict == "proven" and violation is None


def test_classify_property_failed_by_smtbmc():
    verdict, violation = check_formal.classify_property(
        "P", "REQ", CASE_FAILED, {"basecase": "FAIL", "induction": "FAIL"},
        "FAIL", 20)
    assert verdict == "failed"
    assert violation["kind"] == "property_failed"
    assert violation["severity"] == "error"


def test_classify_property_engine_disagreement():
    # smtbmc claims proven, but abc pdr found a counterexample it did not -
    # never trusted silently.
    verdict, violation = check_formal.classify_property(
        "P", "REQ", CASE_OK, {"basecase": "pass", "induction": "pass"},
        "FAIL", 20)
    assert verdict == "failed"
    assert violation["kind"] == "engine_disagreement"


def test_classify_property_bounded_when_induction_does_not_converge(capsys):
    # docs/design.md section 2: "A property that only reaches a bounded
    # depth is recorded as bounded with its depth, and the gate never
    # reports it as proven."
    verdict, violation = check_formal.classify_property(
        "P", "REQ", CASE_OK, {"basecase": "pass", "induction": None},
        "PASS", 20)
    assert verdict == "bounded"
    assert violation["kind"] == "bounded_not_proven"
    assert violation["severity"] == "info"
    assert violation["depth"] == 20
    gate_result = gate.evaluate(
        "formal", _formal_gate_row(),
        {"violations": [violation], "counts": {"total": 1}})
    assert gate_result["status"] == "pass"  # info severity never fails the gate


def test_classify_property_pdr_error_raises():
    # pdr's own DONE reached ERROR (a solver crash) - never silently
    # dropped through to "bounded" just because smtbmc's basecase passed.
    import pytest
    from checklib import CheckError
    with pytest.raises(CheckError):
        check_formal.classify_property(
            "P", "REQ", CASE_OK, {"basecase": "pass", "induction": "pass"},
            "ERROR", 20)


def test_classify_property_pdr_unknown_raises():
    import pytest
    from checklib import CheckError
    with pytest.raises(CheckError):
        check_formal.classify_property(
            "P", "REQ", CASE_OK, {"basecase": "pass", "induction": None},
            "UNKNOWN", 20)


def test_classify_property_no_verdict_at_all_is_an_error():
    import pytest
    from checklib import CheckError
    with pytest.raises(CheckError):
        check_formal.classify_property(
            "P", "REQ", CASE_OK, {"basecase": None, "induction": None},
            "PASS", 20)
