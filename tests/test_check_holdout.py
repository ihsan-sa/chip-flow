"""engine/scripts/check_holdout.py: the holdout gate (docs/design.md 1.5's
holdout row). Runs REAL cocotb-on-Icarus, same caveats as
test_check_sim.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_holdout  # noqa: E402

RTL_GOOD = """\
module top (input wire clk, input wire rst, output reg [1:0] count);
  always @(posedge clk) count <= rst ? 2'd0 : count + 2'd1;
endmodule
"""

# a bug the visible tests never look at, only the held-out one does (mirrors
# gates.yaml's "UART parity inverted where the visible tests do not look"):
# reset value is 1, not 0.
RTL_BUGGY_RESET = """\
module top (input wire clk, input wire rst, output reg [1:0] count);
  always @(posedge clk) count <= rst ? 2'd1 : count + 2'd1;
endmodule
"""

SPEC = """\
top: top
requirements:
  - id: REQ-RESET
    text: rst clears count to 0
    check: sim
"""

HOLDOUT_TB = """\
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

# req: REQ-RESET
@cocotb.test()
async def test_reset_is_zero(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    assert int(dut.count.value) == 0
"""

UNTAGGED_TB = """\
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

@cocotb.test()
async def test_untagged(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await RisingEdge(dut.clk)
"""


def make_ws(tmp_path: Path, rtl_text: str, holdout_text: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "spec").mkdir(parents=True)
    (ws / "holdout").mkdir(parents=True)
    (ws / "log").mkdir(parents=True)
    (ws / "rtl" / "top.v").write_text(rtl_text, encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(SPEC, encoding="utf-8")
    (ws / "holdout" / "test_holdout.py").write_text(holdout_text,
                                                     encoding="utf-8")
    return ws


def test_clean_design_passes(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_GOOD, HOLDOUT_TB)
    code = check_holdout.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["tests_passed"] == 1


def test_held_out_bug_reported_by_requirement_id_only(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_BUGGY_RESET, HOLDOUT_TB)
    code = check_holdout.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    v = next(v for v in out["violations"] if v["kind"] == "holdout_failed")
    assert v["refs"] == ["REQ-RESET"]
    # the result names the requirement id only - never the test name/file.
    assert v["file"] is None
    assert v["module"] is None
    assert "test_holdout" not in v["msg"]
    assert "test_reset_is_zero" not in v["msg"]


def test_untagged_holdout_test_is_flagged(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_GOOD, UNTAGGED_TB)
    code = check_holdout.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "untagged_holdout_test" in kinds


def test_no_holdout_modules_is_an_error(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_GOOD, HOLDOUT_TB)
    (ws / "holdout" / "test_holdout.py").unlink()
    code = check_holdout.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "no test_*.py modules" in out["remediation"]
