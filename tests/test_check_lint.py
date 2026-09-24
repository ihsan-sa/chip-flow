"""engine/scripts/check_lint.py: the lint gate (docs/design.md 1.5's lint
row). Runs the REAL verilator through bin/eda - not hermetic, needs the eda
image - same as tests/check.sh's own verilator-lint smoke."""
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

import check_lint  # noqa: E402
import gate  # noqa: E402
import yaml as _yaml  # noqa: E402

GATES_YAML = ENGINE / "reference" / "gates.yaml"


def _lint_gate_row():
    rows = _yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]["vde"]
    return rows["lint"]

CLEAN_V = """\
module top (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
endmodule
"""

LATCH_V = """\
module top (input wire sel, input wire [1:0] a, output reg [1:0] q);
  always @(*) begin
    if (sel)
      q = a;
  end
endmodule
"""

WIDTH_V = """\
module top (input wire [7:0] a, output reg [3:0] q);
  always @(*) q = a;
endmodule
"""


def make_ws(tmp_path: Path, rtl_text: str, top: str = "top") -> Path:
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "spec").mkdir(parents=True)
    (ws / "rtl" / "top.v").write_text(rtl_text, encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(
        f"top: {top}\nrequirements: []\n", encoding="utf-8")
    return ws


@pytest.mark.slow
def test_clean_design_passes(tmp_path, capsys):
    ws = make_ws(tmp_path, CLEAN_V)
    code = check_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["violations"] == []


@pytest.mark.slow
def test_latch_fails_uncovered_else(tmp_path, capsys):
    # gates.yaml's named fault for `lint`: "an always @* missing an else
    # (latch)".
    ws = make_ws(tmp_path, LATCH_V)
    code = check_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "LATCH" in kinds
    v = next(v for v in out["violations"] if v["kind"] == "LATCH")
    assert v["severity"] == "error"
    assert v["source"] == "verilator"


@pytest.mark.slow
def test_unallowlisted_warning_fails(tmp_path, capsys):
    ws = make_ws(tmp_path, WIDTH_V)
    code = check_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"]: v["severity"] for v in out["violations"]}
    assert kinds.get("WIDTHTRUNC") == "error"


@pytest.mark.slow
def test_allowlisted_warning_downgrades_to_info_and_gate_passes(tmp_path, capsys):
    # check_<gate>.py's own exit reflects "found anything at all" (docs/
    # design.md 1.1: exit 1 = findings); it is gate.py's evaluate(), applying
    # gates.yaml's fail_severities/max_count, that turns an all-"info"
    # violations list into a passing GATE - exactly test_evaluate_pass_and_
    # fail_thresholds in test_gate.py for the warning-severity case.
    ws = make_ws(tmp_path, WIDTH_V)
    (ws / "rtl" / "lint_allow.yaml").write_text(
        "- rule: WIDTHTRUNC\n  reason: intentional narrowing, top 4 bits "
        "of a are a status field read elsewhere\n"
        "- rule: UNUSEDSIGNAL\n  reason: the unused high bits are reserved\n",
        encoding="utf-8")
    code = check_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out          # script level: it still found something
    severities = {v["severity"] for v in out["violations"]}
    assert severities == {"info"}
    reasons = {v["kind"]: v.get("reason") for v in out["violations"]}
    assert reasons["WIDTHTRUNC"]

    gate_result = gate.evaluate("lint", _lint_gate_row(), out)
    assert gate_result["status"] == "pass"   # gate level: no "error" severity


def test_missing_spec_yaml_is_an_error(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "rtl" / "top.v").write_text(CLEAN_V, encoding="utf-8")
    code = check_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert out["status"] == "error"
    assert out["remediation"]


def test_launcher_failure_with_no_percent_lines_is_an_error(
        tmp_path, capsys, monkeypatch):
    # a launcher that exits nonzero without ever reaching verilator (a bad
    # bin/eda, EDA_TOOLCHAIN pointed nowhere, exit 127) used to leave
    # parse_violations nothing to parse -> zero violations -> a clean pass.
    ws = make_ws(tmp_path, CLEAN_V)
    bad_eda = tmp_path / "bad-eda.sh"
    bad_eda.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
    bad_eda.chmod(0o755)
    monkeypatch.setattr(check_lint, "EDA_BIN", bad_eda)
    code = check_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert out["status"] == "error"
    assert "127" in out["remediation"]


def test_no_rtl_files_is_an_error(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: top\nrequirements: []\n", encoding="utf-8")
    code = check_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "no .v/.sv files" in out["remediation"]
