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
                        lambda *a, **k: (fake_output, []))
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


# ---- the standard-cell library is the spec's to choose (a /msde PLL on
# the TT GF template uses mcu7t5v0 at 3.3 V; synth used to hardcode
# mcu9t5v0 5 V and ignore any spec key).

DLY_V = """\
module top (input wire clk, input wire rst, output reg [3:0] count,
            output wire d);
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
  gf180mcu_fd_sc_mcu7t5v0__dlya_1 u0 (.I(count[0]), .Z(d));
endmodule
"""

# the PLL track's workaround: a blackbox stub so yosys elaborates a cell it
# was never told about - it must not hide a cell the chosen lib lacks.
STUB_V = DLY_V + """\
(* blackbox *)
module gf180mcu_fd_sc_mcu7t5v0__dlya_1 (input wire I, output wire Z);
endmodule
"""


def _spec(ws: Path, extra: str) -> None:
    (ws / "spec" / "spec.yaml").write_text(
        f"top: top\nrequirements: []\n{extra}", encoding="utf-8")


def test_choose_std_cell_defaults_and_spec_key():
    assert check_synth.choose_std_cell({"top": "t"}) == {
        "library": "gf180mcu_fd_sc_mcu9t5v0", "corner": "tt_025C_5v00",
        "source": "default"}
    # a TT target gets the TT GF template's own synthesis liberty
    assert check_synth.choose_std_cell({"tt_pins": {"clk": "clk"}}) == {
        "library": "gf180mcu_fd_sc_mcu7t5v0", "corner": "tt_025C_3v30",
        "source": "tt_template"}
    got = check_synth.choose_std_cell(
        {"std_cell": {"library": "gf180mcu_fd_sc_mcu7t5v0",
                      "corner": "ss_125C_3v00"}})
    assert (got["library"], got["corner"]) == (
        "gf180mcu_fd_sc_mcu7t5v0", "ss_125C_3v00")
    # the spec's key outranks the TT default
    got = check_synth.choose_std_cell(
        {"tt_pins": {"clk": "clk"},
         "std_cell": {"library": "gf180mcu_fd_sc_mcu9t5v0"}})
    assert got["library"] == "gf180mcu_fd_sc_mcu9t5v0"


@pytest.mark.parametrize("bad", [
    {"library": "sky130_fd_sc_hd"},
    {"library": "gf180mcu_fd_sc_mcu7t5v0", "corner": "../../etc"},
    {"library": "gf180mcu_fd_sc_mcu7t5v0", "voltage": 3.3},
    "gf180mcu_fd_sc_mcu7t5v0",
])
def test_choose_std_cell_refuses_a_bad_key(bad):
    with pytest.raises(check_synth.CheckError):
        check_synth.choose_std_cell({"std_cell": bad})


@pytest.mark.slow
def test_spec_asking_for_mcu7t5v0_gets_that_lib(tmp_path, capsys):
    ws = make_ws(tmp_path, DLY_V)
    _spec(ws, "std_cell: {library: gf180mcu_fd_sc_mcu7t5v0, "
              "corner: tt_025C_3v30}\n")
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["std_cell"]["liberty"] == \
        "gf180mcu_fd_sc_mcu7t5v0__tt_025C_3v30.lib"
    assert all(c.startswith("gf180mcu_fd_sc_mcu7t5v0__") for c in out["cells"])
    # the instantiated delay cell is kept, no blackbox stub needed
    assert out["cells"]["gf180mcu_fd_sc_mcu7t5v0__dlya_1"] == 1


@pytest.mark.slow
def test_cell_missing_from_the_chosen_lib_fails_loudly(tmp_path, capsys):
    ws = make_ws(tmp_path, DLY_V)
    _spec(ws, "std_cell: {library: gf180mcu_fd_sc_mcu9t5v0}\n")
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    v = next(v for v in out["violations"] if v["kind"] == "cell_not_in_liberty")
    assert "gf180mcu_fd_sc_mcu7t5v0__dlya_1" in v["msg"]
    assert "mcu9t5v0" in v["msg"]


@pytest.mark.slow
def test_blackbox_stub_does_not_hide_a_missing_cell(tmp_path, capsys):
    ws = make_ws(tmp_path, STUB_V)
    _spec(ws, "std_cell: {library: gf180mcu_fd_sc_mcu9t5v0}\n")
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert [v["kind"] for v in out["violations"]] == ["cell_not_in_liberty"]
    # same stub, the lib that has the cell: a clean pass
    _spec(ws, "tt_pins: {clk: clk}\n")
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["std_cell"]["source"] == "tt_template"


# ---- a hierarchical design (top + submodules): synth flattens, so the
# design's own modules are never counted as cells (they used to come back
# cell_not_in_liberty, one per submodule plus stat's "submodules" row),
# while a genuinely foreign cell inside a submodule still fails.

HIER_V = """\
module top (input wire clk, input wire rst, output wire [3:0] count,
            output wire d);
  wire [3:0] c;
  ctr u_ctr (.clk(clk), .rst(rst), .count(c));
  dly u_dly (.a(c[0]), .z(d));
  assign count = c;
endmodule
module ctr (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
endmodule
module dly (input wire a, output wire z);
  (* keep *) wire mid;
  (* keep *) gf180mcu_fd_sc_mcu7t5v0__dlya_1 u_d0 (.I(a), .Z(mid));
  (* keep *) gf180mcu_fd_sc_mcu7t5v0__dlya_1 u_d1 (.I(mid), .Z(z));
endmodule
"""

# the same hierarchy, but a submodule instantiates a cell no gf180mcu
# library has, behind a blackbox stub so yosys elaborates it.
HIER_FOREIGN_V = HIER_V.replace(
    "gf180mcu_fd_sc_mcu7t5v0__dlya_1 u_d1", "foreign_dly u_d1") + """\
(* blackbox *)
module foreign_dly (input wire I, output wire Z);
endmodule
"""

TT_LIB = "std_cell: {library: gf180mcu_fd_sc_mcu7t5v0, corner: tt_025C_3v30}\n"


@pytest.mark.slow
def test_hierarchical_design_passes_and_keeps_its_cells(tmp_path, capsys):
    ws = make_ws(tmp_path, HIER_V)
    _spec(ws, TT_LIB + "must_keep: [u_d0, u_d1]\n")
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["violations"] == []
    assert all(c.startswith("gf180mcu_fd_sc_mcu7t5v0__") for c in out["cells"])
    assert out["cells"]["gf180mcu_fd_sc_mcu7t5v0__dlya_1"] == 2
    # the counter's flops inside u_ctr are counted, not hidden behind it
    assert any("dff" in c for c in out["cells"])
    assert (ws / "synth" / "top.json").is_file()


@pytest.mark.slow
def test_hierarchical_design_with_a_foreign_cell_fails(tmp_path, capsys):
    ws = make_ws(tmp_path, HIER_FOREIGN_V)
    _spec(ws, TT_LIB)
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert [(v["kind"], "foreign_dly" in v["msg"])
            for v in out["violations"]] == [("cell_not_in_liberty", True)]


@pytest.mark.slow
def test_must_keep_name_lost_in_synth_fails(tmp_path, capsys):
    ws = make_ws(tmp_path, HIER_V)
    _spec(ws, TT_LIB + "must_keep: [u_d0, u_gone]\n")
    code = check_synth.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    v = [v for v in out["violations"] if v["kind"] == "must_keep_removed"]
    assert len(v) == 1 and "u_gone" in v[0]["msg"]
    assert "u_d0" not in v[0]["msg"]


@pytest.mark.parametrize("bad", ["u_d0", [""], [3]])
def test_must_keep_must_be_a_list_of_names(bad):
    with pytest.raises(check_synth.CheckError):
        check_synth.spec_must_keep({"must_keep": bad})
    assert check_synth.spec_must_keep({}) == []
