"""engine/scripts/check_mutate.py: the mutate gate (docs/design.md 1.5's
mutate row). Drives REAL mcy + yosys + cocotb-on-Icarus through the eda
image - not hermetic, needs `eda python -m pytest`, and is the slowest gate
in the suite (one build+sim per mutant); kept to a small --size here so the
whole file stays well under tests/check.sh's budget."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_mutate  # noqa: E402

RTL = """\
module top (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
endmodule
"""

SPEC = """\
top: top
requirements:
  - id: REQ-WRAP
    text: count wraps 15 -> 0
    check: sim
ports:
  clk: {dir: input}
  rst: {dir: input}
  count: {dir: output}
"""

# a real, discriminating testbench: checks every cycle's value. Drives and
# samples right after a FallingEdge, never RisingEdge (same discipline as
# corpus/vde/counter8/tb/test_counter8.py's own note): a write scheduled
# right at a RisingEdge races the DUT's own posedge NBA update, and check_
# mutate.py's own check_baseline() now catches exactly that kind of
# flakiness - this fixture used to fail mcy's "-none" baseline (an
# unrelated race, not a real testbench defect) under the yosys techmap
# round-trip mutate_runner.py puts every mutant, baseline included,
# through.
TB_STRONG = """\
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

# req: REQ-WRAP
@cocotb.test()
async def test_counts_every_cycle(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    prev = int(dut.count.value)
    for _ in range(10):
        await FallingEdge(dut.clk)
        cur = int(dut.count.value)
        assert cur == (prev + 1) % 16, f"expected {(prev + 1) % 16} got {cur}"
        prev = cur
"""

# gates.yaml's named fault for `mutate`: "a testbench that asserts nothing".
TB_ASSERTS_NOTHING = """\
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

# req: REQ-WRAP
@cocotb.test()
async def test_does_nothing(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for _ in range(10):
        await RisingEdge(dut.clk)
"""

# a tb that doesn't even import - every mutant, baseline included, crashes
# in mutate_runner.py's own build/test step and is scored "FAIL"/"KILLED"
# for a reason that has nothing to do with the design.
TB_SYNTAX_ERROR = """\
import cocotb

def broken(
"""

# small on purpose: this is the slow gate, and the unit test only needs
# enough mutants to exercise the kill-rate machinery, not statistical rigor.
SMALL_SIZE = 6


def make_ws(tmp_path: Path, tb_text: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "spec").mkdir(parents=True)
    (ws / "tb").mkdir(parents=True)
    (ws / "log").mkdir(parents=True)
    (ws / "rtl" / "top.v").write_text(RTL, encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(SPEC, encoding="utf-8")
    (ws / "tb" / "test_top.py").write_text(tb_text, encoding="utf-8")
    return ws


def test_strong_testbench_kills_most_mutants(tmp_path, capsys):
    ws = make_ws(tmp_path, TB_STRONG)
    code = check_mutate.main(["--workspace", str(ws), "--size", str(SMALL_SIZE),
                              "--seed", "1"])
    out = json.loads(capsys.readouterr().out)
    assert out["total_mutants"] > 0, out
    assert out["kill_rate"] >= 0.5, out
    assert "wall_s" in out
    assert code in (0, 1)   # a real seed may or may not clear 0.9 at size=6


def test_testbench_that_asserts_nothing_fails_mutate(tmp_path, capsys):
    ws = make_ws(tmp_path, TB_ASSERTS_NOTHING)
    code = check_mutate.main(["--workspace", str(ws), "--size", str(SMALL_SIZE),
                              "--seed", "1"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["kill_rate"] == 0.0, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "kill_rate_below_threshold" in kinds


def test_tb_that_does_not_import_fails_via_the_baseline(tmp_path, capsys):
    # every crashed run counts as a "kill" under mcy's own [logic] block -
    # without check_baseline(), this passed at kill_rate 1.0.
    ws = make_ws(tmp_path, TB_SYNTAX_ERROR)
    code = check_mutate.main(["--workspace", str(ws), "--size", str(SMALL_SIZE),
                              "--seed", "1"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert out["status"] == "error"
    assert "unmutated design fails the visible tests" in out.get("remediation", "")


def test_no_tb_modules_is_an_error(tmp_path, capsys):
    ws = make_ws(tmp_path, TB_STRONG)
    (ws / "tb" / "test_top.py").unlink()
    code = check_mutate.main(["--workspace", str(ws), "--size", str(SMALL_SIZE)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "no test_*.py modules" in out["remediation"]


def test_classify_reset_removed_and_output_stuck_and_inverted():
    ports = {"count"}
    assert check_mutate.classify(
        "mutate -mode const0 -module top -cell x -port Q -wire rst", ports
    ) == "reset_removed"
    assert check_mutate.classify(
        "mutate -mode const1 -module top -cell x -port Q -wire count", ports
    ) == "output_stuck"
    assert check_mutate.classify(
        "mutate -mode inv -module top -cell x -port S", ports
    ) == "condition_inverted"
    assert check_mutate.classify(
        "mutate -mode cnot0 -module top -cell x -port A -ctrlbit 0", ports
    ) == "conditional_stuck"
    assert check_mutate.classify("mutate -mode none", ports) is None
