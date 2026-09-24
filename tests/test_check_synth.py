"""engine/scripts/check_synth.py: the synth gate (docs/design.md 1.5's
`synth` row, "### M3."). Runs REAL yosys (synth + dfflibmap/abc against the
gf180mcu liberty) through the eda image - not hermetic, needs the eda image,
same recipe tests/check.sh's own yosys-synth-gf180mcu smoke uses."""
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

import check_synth  # noqa: E402
import gate  # noqa: E402
import yaml as _yaml  # noqa: E402

GATES_YAML = ENGINE / "reference" / "gates.yaml"


def _synth_gate_row():
    rows = _yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]["vde"]
    return rows["synth"]


CLEAN_V = """\
module top (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
endmodule
"""

# gates.yaml's own named fault for `synth`: "a combinational loop". `lp`
# feeds `count`'s own next-state calculation (an output) so it survives
# yosys's dead-code elimination - proved empirically while building this
# gate (an unread loop wire is optimized away before the loop check ever
# sees it).
LOOP_V = """\
module top (input wire clk, input wire rst, output reg [3:0] count);
  wire lp;
  assign lp = lp ^ count[0];
  always @(posedge clk)
    count <= rst ? 4'd0 : (count + 4'd1) ^ {3'd0, lp};
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
def test_clean_design_synths(tmp_path, capsys):
    ws = make_ws(tmp_path, CLEAN_V)
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["violations"] == []
    assert out["area"] and out["area"] > 0
    assert any(c.startswith("gf180mcu_fd_sc_mcu9t5v0__") for c in out["cells"])
    assert (ws / "synth" / "top.v").is_file()


@pytest.mark.slow
def test_combinational_loop_fails(tmp_path, capsys):
    ws = make_ws(tmp_path, LOOP_V)
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "combinational_loop" in kinds
    gate_result = gate.evaluate("synth", _synth_gate_row(), out)
    assert gate_result["status"] == "fail"


# a net read but never driven (`dangling` here) - proved empirically to
# print through yosys's ORDINARY synth/opt flow, same as LOOP_V's own loop
# (no `check -assert` needed, and none run: see check_synth.py's own header
# on why that specific pass was rejected).
NO_DRIVER_V = """\
module top (input wire clk, input wire rst, output reg [3:0] count,
            output wire dangling);
  wire [3:0] unused_net;
  assign dangling = unused_net[0];
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
endmodule
"""


@pytest.mark.slow
def test_undriven_net_fails(tmp_path, capsys):
    ws = make_ws(tmp_path, NO_DRIVER_V)
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "no_driver" in kinds
    gate_result = gate.evaluate("synth", _synth_gate_row(), out)
    assert gate_result["status"] == "fail"


def test_empty_netlist_is_refused(tmp_path, capsys, monkeypatch):
    # cell_histogram returns {} whenever the LAST "<N> <area> cells" summary
    # line reports zero rows under it - defense in depth, since a
    # genuinely-zero-cell design never reaches this far in practice
    # (run_yosys's own "no area line" guard refuses it first, proved
    # empirically: yosys's `stat -liberty` prints neither a cells summary
    # nor a Chip area line at all for a true zero-cell design). Faked
    # directly so the explicit empty-netlist refusal is tested on its own.
    ws = make_ws(tmp_path, CLEAN_V)
    fake_output = ("        1 wires\n"
                  "        0 0.0 cells\n\n"
                  "   Chip area for module '\\top': 0.0\n")
    monkeypatch.setattr(check_synth, "run_yosys",
                        lambda *a, **k: fake_output)
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no cells" in out["remediation"]


def test_missing_spec_yaml_is_an_error(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "rtl" / "top.v").write_text(CLEAN_V, encoding="utf-8")
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert out["status"] == "error"


def test_no_rtl_files_is_an_error(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: top\nrequirements: []\n", encoding="utf-8")
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "no .v/.sv files" in out["remediation"]


def test_launcher_failure_is_an_error(tmp_path, capsys, monkeypatch):
    ws = make_ws(tmp_path, CLEAN_V)
    bad_eda = tmp_path / "bad-eda.sh"
    bad_eda.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
    bad_eda.chmod(0o755)
    monkeypatch.setattr(check_synth, "EDA_BIN", bad_eda)
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert out["status"] == "error"


def test_cell_histogram_flags_unmapped_cells():
    output = """\
        2 wires
       28 1.09E+03 cells
        3   84.672   gf180mcu_fd_sc_mcu9t5v0__and3_1
        1       -    $_DFF_P_

   Chip area for module '\\top': 100.0
"""
    cells = check_synth.cell_histogram(output)
    assert cells["$_DFF_P_"] == 1
    assert cells["gf180mcu_fd_sc_mcu9t5v0__and3_1"] == 3


def test_cell_histogram_flags_latches_not_delay_cells():
    output = """\
        3 1.00E+02 cells
        1   50.0   gf180mcu_fd_sc_mcu9t5v0__latq_1
        2   25.0   gf180mcu_fd_sc_mcu9t5v0__dlya_1

   Chip area for module '\\top': 100.0
"""
    cells = check_synth.cell_histogram(output)
    latches = [c for c in cells if check_synth.LATCH_RE.search(c)]
    assert latches == ["gf180mcu_fd_sc_mcu9t5v0__latq_1"]
    assert not any(check_synth.LATCH_RE.search(c) for c in cells if "dly" in c)


def test_cell_histogram_uses_the_last_cells_section():
    # `synth`'s own internal passes print more than one such section (a
    # generic pre-abc breakdown, then the post-abc/liberty one this gate
    # wants) - only the LAST is counted.
    output = """\
        1 0 cells
        1     -    $_NOT_

        1 1.00E+01 cells
        1   10.0   gf180mcu_fd_sc_mcu9t5v0__clkinv_1

   Chip area for module '\\top': 10.0
"""
    cells = check_synth.cell_histogram(output)
    assert cells == {"gf180mcu_fd_sc_mcu9t5v0__clkinv_1": 1}
