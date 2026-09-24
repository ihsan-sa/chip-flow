"""engine/scripts/check_glsim.py: the glsim gate (docs/design.md 1.5's
glsim row, "### M4."). Fakes cocotb's own runner (never real Icarus/PDK
gate-level models - tests/smoke-harden.sh runs the real thing) to prove the
gate's failure classification and its "every test passes both ways" rule.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_glsim  # noqa: E402


def make_ws(tmp_path: Path, with_sdf: bool = True, with_nl: bool = True) -> Path:
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\n"
        "ports: {clk: {dir: input, width: 1}}\n"
        "tt_pins: {clk: clk}\n", encoding="utf-8")
    (ws / "tb").mkdir(parents=True)
    (ws / "tb" / "test_counter8.py").write_text(
        "import cocotb\n@cocotb.test()\nasync def test_x(dut):\n    pass\n",
        encoding="utf-8")
    final = ws / "harden" / "runs" / "run" / "final"
    if with_nl:
        (final / "nl").mkdir(parents=True)
        (final / "nl" / "tt_um_counter8.nl.v").write_text("x", encoding="utf-8")
    else:
        final.mkdir(parents=True)
    if with_sdf:
        (final / "sdf" / check_glsim.SDF_CORNER).mkdir(parents=True)
        (final / "sdf" / check_glsim.SDF_CORNER /
        f"tt_um_counter8__{check_glsim.SDF_CORNER}.sdf").write_text("x", encoding="utf-8")
    return ws


def _patch_pdk(monkeypatch, tmp_path):
    monkeypatch.setattr(check_glsim, "_pdk_root",
                        lambda: Path("/fake/toolchain/foss/pdks"))
    prim = tmp_path / "primitives.v"
    cells = tmp_path / "cells.v"
    prim.write_text("x", encoding="utf-8")
    cells.write_text("x", encoding="utf-8")
    monkeypatch.setattr(check_glsim, "_cell_sources", lambda pdk_root: [prim, cells])


def test_no_harden_output_refuses(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\n"
        "ports: {clk: {dir: input, width: 1}}\ntt_pins: {clk: clk}\n",
        encoding="utf-8")
    code = check_glsim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "has not run yet" in out["error"]


def test_no_sdf_refuses(tmp_path, capsys):
    ws = make_ws(tmp_path, with_sdf=False)
    code = check_glsim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no SDF" in out["error"]


def test_no_netlist_refuses_inside_run_pass(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path, with_nl=False)
    _patch_pdk(monkeypatch, tmp_path)
    code = check_glsim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no gate-level netlist" in out["error"]


def test_no_results_xml_is_an_error(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    _patch_pdk(monkeypatch, tmp_path)

    class FakeRunner:
        def build(self, **kw):
            pass

        def test(self, **kw):
            pass  # never writes results_xml

    monkeypatch.setattr("cocotb_tools.runner.get_runner", lambda name: FakeRunner())
    code = check_glsim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "produced no" in out["error"]


def test_failing_test_in_either_pass_is_a_violation(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    _patch_pdk(monkeypatch, tmp_path)

    FAIL_XML = """<?xml version="1.0"?>
<testsuites><testsuite><testcase name="test_counter8.test_x" classname="test_counter8">
<failure message="mismatch"/></testcase></testsuite></testsuites>"""
    PASS_XML = """<?xml version="1.0"?>
<testsuites><testsuite><testcase name="test_counter8.test_x" classname="test_counter8">
</testcase></testsuite></testsuites>"""

    class FakeRunner:
        def build(self, **kw):
            pass

        def test(self, results_xml, **kw):
            content = FAIL_XML if "functional" in results_xml else PASS_XML
            Path(results_xml).write_text(content, encoding="utf-8")

    monkeypatch.setattr("cocotb_tools.runner.get_runner", lambda name: FakeRunner())
    code = check_glsim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["status"] == "violations"
    passes = {v["glsim_pass"] for v in out["violations"]}
    assert passes == {"functional"}
    assert out["results"]["sdf"]["test_x"] is True
    assert out["results"]["functional"]["test_x"] is False


def test_both_passes_all_green_is_a_pass(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    _patch_pdk(monkeypatch, tmp_path)

    PASS_XML = """<?xml version="1.0"?>
<testsuites><testsuite><testcase name="test_counter8.test_x" classname="test_counter8">
</testcase></testsuite></testsuites>"""

    class FakeRunner:
        def build(self, **kw):
            pass

        def test(self, results_xml, **kw):
            Path(results_xml).write_text(PASS_XML, encoding="utf-8")

    monkeypatch.setattr("cocotb_tools.runner.get_runner", lambda name: FakeRunner())
    code = check_glsim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["results"]["functional"]["test_x"] is True
    assert out["results"]["sdf"]["test_x"] is True
