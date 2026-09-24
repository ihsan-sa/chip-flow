"""engine/scripts/check_cover.py: the cover gate (docs/design.md 1.5's
`cover` row, "### M3."). Runs the REAL cocotb tests over the VERILATOR
simulator (--coverage) through the eda image - not hermetic, needs the eda
image, and the slowest gate in this file (a verilator build per test) so
kept to a small design."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_cover  # noqa: E402
import gate  # noqa: E402
import yaml as _yaml  # noqa: E402

GATES_YAML = ENGINE / "reference" / "gates.yaml"


def _cover_gate_row():
    rows = _yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]["vde"]
    return rows["cover"]


RTL = """\
module top (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
endmodule
"""

# gates.yaml's own named fault for `cover`: "an unreachable state" - the
# extra branch's guard contradicts itself, so no test (however thorough)
# can ever reach it.
RTL_UNREACHABLE = """\
module top (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) begin
    if (rst)
      count <= 4'd0;
    else if (rst && !rst)
      count <= 4'hA;
    else
      count <= count + 4'd1;
  end
endmodule
"""

TB = """\
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

@cocotb.test()
async def test_counts_every_cycle(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    prev = int(dut.count.value)
    for _ in range(20):
        await FallingEdge(dut.clk)
        cur = int(dut.count.value)
        assert cur == (prev + 1) % 16
        prev = cur
"""


def make_ws(tmp_path: Path, rtl_text: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "spec").mkdir(parents=True)
    (ws / "tb").mkdir(parents=True)
    (ws / "log").mkdir(parents=True)
    (ws / "rtl" / "top.v").write_text(rtl_text, encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(
        "top: top\nrequirements: []\n", encoding="utf-8")
    (ws / "tb" / "test_top.py").write_text(TB, encoding="utf-8")
    return ws


def test_full_coverage_passes(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL)
    code = check_cover.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["line_pct"] == 100.0
    assert out["toggle_pct"] == 100.0
    # coverage.dat must never linger in tb/ - it would flip tb's own
    # dir_text hash (invalidation.yaml) for sim/mutate/holdout too.
    assert not (ws / "tb" / "coverage.dat").exists()


def test_unreachable_state_fails_the_threshold(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_UNREACHABLE)
    code = check_cover.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    errors = {v["kind"] for v in out["violations"] if v["severity"] == "error"}
    assert "line_coverage_below_threshold" in errors
    infos = {v["kind"] for v in out["violations"] if v["severity"] == "info"}
    assert "line_not_covered" in infos
    gate_result = gate.evaluate("cover", _cover_gate_row(), out)
    assert gate_result["status"] == "fail"


def test_exclusion_removes_a_point_from_the_denominator():
    # count_coverage() is the aggregation/threshold math on its own - pure,
    # no real verilator+cocotb build needed (tests/check.sh's own two-
    # minute budget, docs/design.md 1.1, is why this is not a third real
    # end-to-end run like the two above).
    per_file = {"top.v": {"line": {5: 3, 6: 0, 7: 3}, "toggle": {}}}
    violations, counts = check_cover.count_coverage(
        per_file, {"top.v"}, {"line": {}, "toggle": {}}, 95.0, 90.0)
    assert counts["line_pct"] < 95.0
    assert any(v["kind"] == "line_coverage_below_threshold" for v in violations)

    excludes = {"line": {("top.v", 6): "guard is intentionally dead, kept "
                         "for a future revision"}, "toggle": {}}
    violations2, counts2 = check_cover.count_coverage(
        per_file, {"top.v"}, excludes, 95.0, 90.0)
    assert counts2["line_pct"] == 100.0
    assert counts2["line_total"] == 2  # the excluded line drops out entirely
    assert violations2 == []


def test_crashed_coverage_info_launcher_is_an_error(tmp_path, monkeypatch):
    # a failed launcher (nonzero exit, no output) fails the gate - unit-
    # level (no real cocotb+verilator build needed): write_coverage_info()
    # is the one piece of run_coverage() that shells out through bin/eda.
    from checklib import CheckError
    import pytest
    bad_eda = tmp_path / "bad-eda.sh"
    bad_eda.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
    bad_eda.chmod(0o755)
    monkeypatch.setattr(check_cover, "EDA_BIN", bad_eda)
    fake_dat = tmp_path / "coverage.dat"
    fake_dat.write_text("# SystemC::Coverage-3\n", encoding="utf-8")
    with pytest.raises(CheckError, match="127"):
        check_cover.write_coverage_info(tmp_path, fake_dat)


def test_no_tb_modules_is_an_error(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL)
    (ws / "tb" / "test_top.py").unlink()
    code = check_cover.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "no test_*.py modules" in out["remediation"]


def test_bad_exclusion_entry_is_an_error(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL)
    (ws / "rtl" / "cover_exclude.yaml").write_text(
        "- file: top.v\n  line: 6\n", encoding="utf-8")  # no reason
    code = check_cover.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "reason" in out["remediation"]


def test_parse_info_distinguishes_toggle_from_branch_labels(tmp_path):
    info = tmp_path / "c.info"
    info.write_text(
        "SF:top.v\n"
        "DA:6,3\n"
        "BRDA:6,0,clk:0->1,3\n"
        "BRDA:7,0,if,1\n"
        "BRDA:7,0,else,0\n"
        "end_of_record\n", encoding="utf-8")
    parsed = check_cover.parse_info(info)
    data = parsed["top.v"]
    assert data["line"] == {6: 3}
    assert data["toggle"] == {(6, "clk:0->1"): 3}  # the "if"/"else" branch
    # labels never match TOGGLE_LABEL_RE and are correctly left out.


def test_parse_info_empty_report_is_an_error(tmp_path):
    from checklib import CheckError
    import pytest
    info = tmp_path / "empty.info"
    info.write_text("", encoding="utf-8")
    with pytest.raises(CheckError):
        check_cover.parse_info(info)
