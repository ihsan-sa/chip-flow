"""engine/scripts/check_mutate.py: the mutate gate (docs/design.md 1.5's
mutate row). Drives REAL mcy + yosys + cocotb-on-Icarus through the eda
image - not hermetic, needs `eda python -m pytest`, and is the slowest gate
in the suite (one build+sim per mutant); kept to a small --size here so the
whole file stays well under tests/check.sh's budget."""
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


@pytest.mark.slow
def test_strong_testbench_kills_most_mutants(tmp_path, capsys):
    ws = make_ws(tmp_path, TB_STRONG)
    code = check_mutate.main(["--workspace", str(ws), "--size", str(SMALL_SIZE),
                              "--seed", "1"])
    out = json.loads(capsys.readouterr().out)
    assert out["total_mutants"] > 0, out
    assert out["kill_rate"] >= 0.5, out
    assert "wall_s" in out
    assert code in (0, 1)   # a real seed may or may not clear 0.9 at size=6


@pytest.mark.slow
def test_testbench_that_asserts_nothing_fails_mutate(tmp_path, capsys):
    ws = make_ws(tmp_path, TB_ASSERTS_NOTHING)
    code = check_mutate.main(["--workspace", str(ws), "--size", str(SMALL_SIZE),
                              "--seed", "1"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["kill_rate"] == 0.0, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "kill_rate_below_threshold" in kinds


@pytest.mark.slow
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


# ---- equivalent mutants ----------------------------------------------------
# y is registered (a == 5). 5 is 4'b0101, so forcing B's bit 0 to 1 is the
# value it already has (equivalent - no test can kill it), and forcing bit 1
# to 1 turns the compare into (a == 7) (a real, killable change).
EQ_RTL = """\
module top (input wire clk, input wire [3:0] a, output reg y);
  always @(posedge clk) y <= (a == 4'd5);
endmodule
"""


def make_design_il(tmp_path: Path) -> tuple[Path, str, str]:
    """design.il the way mcy's own [script] makes it, plus the $eq and
    $dff cell names mutate lines address."""
    import re
    import subprocess
    (tmp_path / "top.v").write_text(EQ_RTL, encoding="utf-8")
    il = tmp_path / "design.il"
    subprocess.run([str(check_mutate.EDA_BIN), "yosys", "-q", "-p",
                    f"read_verilog -sv {tmp_path / 'top.v'}; hierarchy -top "
                    f"top; proc; write_rtlil {il}"], check=True)
    text = il.read_text(encoding="utf-8")
    eq = re.search(r"cell \$eq (\S+)", text).group(1)
    dff = re.search(r"cell \$dff (\S+)", text).group(1)
    return il, eq, dff


@pytest.mark.slow
def test_equivalent_mutant_is_proven(tmp_path):
    il, eq, _ = make_design_il(tmp_path)
    proven, why = check_mutate.prove_equivalent(
        il, f"mutate -mode const1 -module top -cell {eq} -port B -portbit 0",
        tmp_path / "m")
    assert proven, why
    assert why == check_mutate.PROVEN + "signal induction"


@pytest.mark.slow
def test_non_equivalent_survivor_is_not_proven(tmp_path):
    il, eq, _ = make_design_il(tmp_path)
    proven, why = check_mutate.prove_equivalent(
        il, f"mutate -mode const1 -module top -cell {eq} -port B -portbit 1",
        tmp_path / "m")
    assert not proven, why
    # a base-case counterexample is a real difference: no pdr attempt
    assert "pdr" not in why and not (tmp_path / "m" / "pdr").exists()


@pytest.mark.slow
def test_inverted_clock_is_outside_the_proof(tmp_path):
    # the sat model steps every FF on one implicit clock and never reads a
    # CLK net, so the equiv flow alone "proves" this one - single_clock()
    # is what refuses it.
    il, _, dff = make_design_il(tmp_path)
    proven, why = check_mutate.prove_equivalent(
        il, f"mutate -mode inv -module top -cell {dff} -port CLK -portbit 0",
        tmp_path / "m")
    assert not proven
    assert "clock" in why


@pytest.mark.slow
def test_yosys_error_and_timeout_are_not_proofs(tmp_path):
    il, eq, _ = make_design_il(tmp_path)
    proven, why = check_mutate.prove_equivalent(
        il, "mutate -mode const1 -module top -cell nosuchcell -port B "
        "-portbit 0", tmp_path / "bad")
    assert not proven, why
    proven, why = check_mutate.prove_equivalent(
        il, f"mutate -mode const1 -module top -cell {eq} -port B -portbit 0",
        tmp_path / "slow", timeout=0.01)
    assert not proven
    assert "timeout" in why


# ---- equivalent only on reachable states ------------------------------------
# count runs 0..20 and wraps. 20 is 8'b00010100, so forcing the compare's A
# bit 7 to 0 also wraps at 148 - a value count never reaches (equivalent),
# but the chain 21..147 is far longer than EQUIV_DEPTH, so induction from an
# arbitrary state fails. Forcing A bit 3 to 1 means it never wraps: a real
# difference, but only at cycle ~21, past the base case's depth.
REACH_RTL = """\
module top (input wire clk, input wire rst, output reg [7:0] count);
  always @(posedge clk) count <= (rst || count == 8'd20) ? 8'd0 : count + 8'd1;
endmodule
"""


def make_reach_il(tmp_path: Path) -> tuple[Path, str]:
    import re
    import subprocess
    (tmp_path / "reach.v").write_text(REACH_RTL, encoding="utf-8")
    il = tmp_path / "reach.il"
    subprocess.run([str(check_mutate.EDA_BIN), "yosys", "-q", "-p",
                    f"read_verilog -sv {tmp_path / 'reach.v'}; hierarchy -top "
                    f"top; proc; write_rtlil {il}"], check=True)
    return il, re.search(r"cell \$eq (\S+)", il.read_text()).group(1)


@pytest.mark.slow
def test_reachable_only_equivalent_is_proven_by_pdr(tmp_path):
    il, eq = make_reach_il(tmp_path)
    proven, why = check_mutate.prove_equivalent(
        il, f"mutate -mode const0 -module top -cell {eq} -port A -portbit 7",
        tmp_path / "m")
    assert proven, why
    assert why == check_mutate.PROVEN + "pdr"   # induction alone failed


@pytest.mark.slow
def test_non_equivalent_past_the_base_case_is_not_proven_by_pdr(tmp_path):
    il, eq = make_reach_il(tmp_path)
    proven, why = check_mutate.prove_equivalent(
        il, f"mutate -mode const1 -module top -cell {eq} -port A -portbit 3",
        tmp_path / "m")
    assert not proven
    assert "induction step failed" in why and "pdr: FAIL" in why, why


# ---- hidden state: only the signal-matched induction proves it ------------
# the output is a wrap pulse, so two copies whose counters differ can agree
# on it for up to 20 cycles - longer than EQUIV_DEPTH - and the outputs-only
# induction step fails; with every matched signal compared the counters
# themselves must agree, and the step holds. 20 is 8'b00010100: forcing a
# compare's B bit 0 to 0 changes nothing, forcing it to 1 compares with 21.
HIDDEN_RTL = """\
module top (input wire clk, input wire rst, output wire p);
  reg [7:0] count;
  always @(posedge clk) count <= (rst || count == 8'd20) ? 8'd0 : count + 8'd1;
  assign p = (count == 8'd20);
endmodule
"""


def make_hidden_il(tmp_path: Path) -> tuple[Path, list[str]]:
    import re
    import subprocess
    (tmp_path / "hidden.v").write_text(HIDDEN_RTL, encoding="utf-8")
    il = tmp_path / "hidden.il"
    subprocess.run([str(check_mutate.EDA_BIN), "yosys", "-q", "-p",
                    f"read_verilog -sv {tmp_path / 'hidden.v'}; hierarchy -top "
                    f"top; proc; write_rtlil {il}"], check=True)
    return il, re.findall(r"cell \$eq (\S+)", il.read_text())


@pytest.mark.slow
def test_hidden_state_equivalent_is_proven_by_signal_induction(tmp_path):
    il, eqs = make_hidden_il(tmp_path)
    assert eqs
    for eq in eqs:
        proven, why = check_mutate.prove_equivalent(
            il, f"mutate -mode const0 -module top -cell {eq} -port B -portbit 0",
            tmp_path / f"eq_{eq.strip(chr(92)).replace('$', '_')}",
            pdr_timeout=5)
        assert proven, why
        assert why == check_mutate.PROVEN + "signal induction"


@pytest.mark.slow
def test_hidden_state_real_difference_is_not_proven(tmp_path):
    il, eqs = make_hidden_il(tmp_path)
    for eq in eqs:
        proven, why = check_mutate.prove_equivalent(
            il, f"mutate -mode const1 -module top -cell {eq} -port B -portbit 0",
            tmp_path / f"ne_{eq.strip(chr(92)).replace('$', '_')}",
            pdr_timeout=30)
        assert not proven, why


@pytest.mark.slow
def test_pdr_timeout_is_not_a_proof(tmp_path):
    il, eq = make_reach_il(tmp_path)
    proven, why = check_mutate.prove_equivalent(
        il, f"mutate -mode const0 -module top -cell {eq} -port A -portbit 7",
        tmp_path / "m", pdr_timeout=0.01)
    assert not proven
    assert "pdr" in why and "timeout" in why.lower(), why


# ---- equivalence is on the outputs only -------------------------------------
# t counts every cycle but only its bit 0 reaches the output. Forcing the
# adder's result bit 2 to 1 changes t's own sequence (an internal register
# that now differs from the reference) yet leaves y identical from reset:
# equivalent, and the old per-signal base case refused exactly this.
# Forcing result bit 0 to 1 sticks t[0], so y differs a cycle after reset.
INTERNAL_RTL = """\
module top (input wire clk, input wire rst, output reg y);
  reg [3:0] t;
  always @(posedge clk) begin
    t <= rst ? 4'd0 : t + 4'd1;
    y <= rst ? 1'b0 : t[0];
  end
endmodule
"""


def make_internal_il(tmp_path: Path) -> tuple[Path, str]:
    import re
    import subprocess
    (tmp_path / "internal.v").write_text(INTERNAL_RTL, encoding="utf-8")
    il = tmp_path / "internal.il"
    subprocess.run([str(check_mutate.EDA_BIN), "yosys", "-q", "-p",
                    f"read_verilog -sv {tmp_path / 'internal.v'}; hierarchy "
                    f"-top top; proc; write_rtlil {il}"], check=True)
    return il, re.search(r"cell \$add (\S+)", il.read_text()).group(1)


@pytest.mark.slow
def test_internal_difference_with_identical_outputs_is_proven(tmp_path):
    il, add = make_internal_il(tmp_path)
    proven, why = check_mutate.prove_equivalent(
        il, f"mutate -mode const1 -module top -cell {add} -port Y -portbit 2",
        tmp_path / "m")
    assert proven, why


@pytest.mark.slow
def test_output_difference_from_reset_is_not_proven(tmp_path):
    il, add = make_internal_il(tmp_path)
    proven, why = check_mutate.prove_equivalent(
        il, f"mutate -mode const1 -module top -cell {add} -port Y -portbit 0",
        tmp_path / "m")
    assert not proven, why
    assert "base case" in why and not (tmp_path / "m" / "pdr").exists(), why


def test_pdr_gets_thirty_minutes_by_default():
    # gate.py and jobs.py call run(["--workspace", ws]) with no options,
    # so this default is what a jobs.py-launched mutate gate gets.
    assert check_mutate.PDR_TIMEOUT_S == 1800
    assert check_mutate.parse_args(["--workspace", "w"]).pdr_timeout == 1800
    assert check_mutate.parse_args(
        ["--workspace", "w", "--pdr-timeout", "60"]).pdr_timeout == 60


def _mutant(mid, cls, killed):
    return {"id": mid, "mutation": f"mutate -mode x {mid}", "class": cls,
            "killed": killed, "survived": not killed, "src": None}


def test_score_excludes_equivalent_mutants_from_the_kill_rate():
    ms = ([_mutant(i, "stuck_other", True) for i in range(2, 11)]
          + [_mutant(11, "stuck_other", False),
             _mutant(12, "output_stuck", False)])
    facts, violations = check_mutate.score(ms, set())
    assert facts["kill_rate"] == round(9 / 11, 4)
    assert "kill_rate_below_threshold" in {v["kind"] for v in violations}

    facts, violations = check_mutate.score(ms, {11, 12})
    assert facts["kill_rate"] == 1.0
    assert facts["scored_mutants"] == 9 and facts["total_mutants"] == 11
    assert facts["equivalent"] == 2 and facts["equivalent_ids"] == [11, 12]
    assert facts["survived"] == 0
    kinds = {v["kind"]: v["severity"] for v in violations}
    # a must-kill class that is proven equivalent is info, not an error
    assert kinds == {"equivalent_stuck_other": "info",
                     "equivalent_output_stuck": "info"}


def test_score_refuses_when_every_mutant_is_equivalent():
    ms = [_mutant(2, "stuck_other", False)]
    with pytest.raises(check_mutate.CheckError, match="nothing left"):
        check_mutate.score(ms, {2})


# --- spec/mutant_rulings.yaml: an owner-ruled survivor, one at a time -----
# run() with mcy, its database and the prover stubbed out: fast, no tools.

def make_stubbed_run(tmp_path, monkeypatch, mutants, rulings, proven=()):
    ws = make_ws(tmp_path, TB_STRONG)
    if rulings is not None:
        (ws / "spec" / "mutant_rulings.yaml").write_text(rulings,
                                                         encoding="utf-8")
    root = tmp_path / "toolchain"
    (root / check_mutate.MCY_REL).parent.mkdir(parents=True)
    (root / check_mutate.MCY_REL).write_text("", encoding="utf-8")
    monkeypatch.setattr(check_mutate, "toolchain_root", lambda: root)
    monkeypatch.setattr(check_mutate, "run_mcy", lambda *a, **k: None)
    monkeypatch.setattr(check_mutate, "check_baseline", lambda *a: None)
    monkeypatch.setattr(check_mutate, "read_mutants", lambda *a: mutants)
    sent: list[int] = []

    def fake_prove(design_il, ms, work, jobs, pdr_timeout=0):
        todo = [m["id"] for m in ms if m["survived"]]
        sent.extend(todo)
        return {mid: (check_mutate.PROVEN + "induction" if mid in proven
                      else "outputs differ from reset (base case)")
                for mid in todo}

    monkeypatch.setattr(check_mutate, "prove_survivors", fake_prove)
    return ws, sent


RULE_11 = """\
equivalent:
  - id: 11
    ruling: "owner, 2026-09-25"
    evidence: "the flop it inverts never reaches an output"
"""


def _nine_killed_plus(*extra):
    return [_mutant(i, "stuck_other", True) for i in range(2, 11)] + list(extra)


def test_a_ruled_survivor_leaves_the_kill_rate_without_the_prover(tmp_path, monkeypatch, capsys):
    ms = _nine_killed_plus(_mutant(11, "output_stuck", False),
                           _mutant(12, "stuck_other", False))
    ws, sent = make_stubbed_run(tmp_path, monkeypatch, ms, RULE_11)
    code = check_mutate.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert sent == [12]                          # 11 never reached the prover
    assert out["equivalent_ids"] == [11]
    assert out["equivalent_by"] == {
        "11": "accepted by owner ruling: owner, 2026-09-25"}
    assert out["kill_rate"] == 0.9               # 9 / (11 - 1)
    sev = {v["kind"]: v["severity"] for v in out["violations"]}
    # the ruled must-kill survivor is info; the unruled one still reported
    assert sev == {"equivalent_output_stuck": "info",
                   "survivor_stuck_other": "info"}
    assert "12" in out["unproven_survivors"]
    assert code == 1, out


def test_an_unruled_must_kill_survivor_still_fails(tmp_path, monkeypatch, capsys):
    ms = _nine_killed_plus(_mutant(11, "output_stuck", False))
    ws, sent = make_stubbed_run(tmp_path, monkeypatch, ms, None)
    check_mutate.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert sent == [11]
    assert out["equivalent_ids"] == []
    assert {v["kind"]: v["severity"] for v in out["violations"]} == {
        "survivor_output_stuck": "error"}


def test_a_ruling_for_a_killed_mutant_is_refused(tmp_path, monkeypatch, capsys):
    ms = _nine_killed_plus(_mutant(11, "output_stuck", True))
    ws, _ = make_stubbed_run(tmp_path, monkeypatch, ms, RULE_11)
    code = check_mutate.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "killed" in out["error"] and out.get("remediation")


def test_a_ruling_for_an_unknown_mutant_is_refused(tmp_path, monkeypatch, capsys):
    ws, _ = make_stubbed_run(tmp_path, monkeypatch, _nine_killed_plus(),
                             RULE_11)
    code = check_mutate.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "not a mutant this run generated" in out["error"]


def test_below_spread_is_refused_for_mutate(tmp_path, monkeypatch, capsys):
    rulings = RULE_11 + """\
below_spread:
  - id: 12
    netlist_line: "x"
    measure: f
    delta: 0.0
    sigma: 0.1
    ruling: "owner, 2026-09-25"
"""
    ms = _nine_killed_plus(_mutant(11, "output_stuck", False))
    ws, sent = make_stubbed_run(tmp_path, monkeypatch, ms, rulings)
    code = check_mutate.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "below_spread" in out["error"] and sent == []
