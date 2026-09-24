"""engine/scripts/check_sim.py: the sim gate (docs/design.md 1.5's sim row).
Runs REAL cocotb-on-Icarus through the eda image - not hermetic, and needs
`eda python -m pytest` (cocotb/cocotb_tools must be importable), same as
tests/check.sh's own cocotb-icarus smoke."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_sim  # noqa: E402

RTL_GOOD = """\
module top (input wire clk, input wire rst, output reg [1:0] count);
  always @(posedge clk) count <= rst ? 2'd0 : count + 2'd1;
endmodule
"""

# "counter wraps one early" (gates.yaml's named fault for `sim`): wraps at 2
# instead of 3.
RTL_BUGGY = """\
module top (input wire clk, input wire rst, output reg [1:0] count);
  always @(posedge clk) count <= rst ? 2'd0 : (count == 2'd2 ? 2'd0 : count + 2'd1);
endmodule
"""

SPEC_ONE_REQ = """\
top: top
requirements:
  - id: REQ-WRAP
    text: count wraps 3 -> 0
    check: sim
"""

SPEC_TWO_REQS = SPEC_ONE_REQ + """\
  - id: REQ-RESET
    text: rst clears count
    check: sim
"""

TB_WRAP = """\
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

# req: REQ-WRAP
@cocotb.test()
async def test_wraps_at_3(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    prev = int(dut.count.value)
    for _ in range(8):
        await RisingEdge(dut.clk)
        cur = int(dut.count.value)
        assert cur == (prev + 1) % 4, f"expected {(prev + 1) % 4} got {cur}"
        prev = cur
"""


def make_ws(tmp_path: Path, rtl_text: str, spec_text: str, tb_text: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "spec").mkdir(parents=True)
    (ws / "tb").mkdir(parents=True)
    (ws / "log").mkdir(parents=True)
    (ws / "rtl" / "top.v").write_text(rtl_text, encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(spec_text, encoding="utf-8")
    (ws / "tb" / "test_top.py").write_text(tb_text, encoding="utf-8")
    return ws


def test_clean_design_all_requirements_covered_passes(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_GOOD, SPEC_ONE_REQ, TB_WRAP)
    code = check_sim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["tests_passed"] == 1


def test_counter_wraps_one_early_is_caught(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_BUGGY, SPEC_ONE_REQ, TB_WRAP)
    code = check_sim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    v = next(v for v in out["violations"] if v["kind"] == "test_failed")
    assert v["refs"] == ["REQ-WRAP"]


def test_requirement_without_a_test_fails_sim(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_GOOD, SPEC_TWO_REQS, TB_WRAP)
    code = check_sim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    v = next(v for v in out["violations"]
             if v["kind"] == "requirement_no_test")
    assert v["refs"] == ["REQ-RESET"]
    # the wrap test itself still runs and passes - only coverage is short.
    assert out["tests_passed"] == 1


def test_no_tb_modules_is_an_error(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_GOOD, SPEC_ONE_REQ, TB_WRAP)
    (ws / "tb" / "test_top.py").unlink()
    code = check_sim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "no test_*.py modules" in out["remediation"]
