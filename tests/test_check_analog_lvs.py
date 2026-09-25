"""engine/scripts/check_analog_lvs.py: the ade `lvs` gate (docs/design.md
1.5, 5, "### M9."). Real magic extraction + netgen LVS, under the PDK's
own setup, against M8's own netlist/<block>.cir (docs/spikes/glayout.md:
"the spike's own LVS only compared gLayout's output with itself... not a
real schematic-vs-layout LVS" - this one is)."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_analog_lvs  # noqa: E402
import gate  # noqa: E402
import yaml as _yaml  # noqa: E402

CORPUS = REPO / "corpus" / "ade"
GATES_YAML = ENGINE / "reference" / "gates.yaml"


def _lvs_gate_row():
    rows = _yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]["ade"]
    return rows["lvs"]


def make_ws(tmp_path: Path, block: str) -> Path:
    """A workspace holding the rung's own generator, M8 netlist, sizing,
    spec and post-layout bench - what faults.py's scratch copy carries."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    for sub in ("layout", "netlist", "sizing", "layout_ref"):
        src = CORPUS / block / sub
        if src.is_dir():
            (ws / sub).mkdir()
            for f in src.iterdir():
                if f.is_file() and f.suffix != ".gds":
                    shutil.copy2(f, ws / sub / f.name)
    (ws / "spec").mkdir()
    shutil.copy2(CORPUS / block / "spec.yaml", ws / "spec" / "spec.yaml")
    ws.joinpath("state.json").write_text(
        json.dumps({"version": 3, "skill": "ade", "block": block}),
        encoding="utf-8")
    return ws


def import_plant(block: str, name: str):
    path = CORPUS / block / "faults" / name
    spec = importlib.util.spec_from_file_location(f"plant_{block}_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.plant


def run_json(args, tmp_path):
    # --out, not capsys - see test_check_analog_drc.py's own run_json for
    # why (a vendor GF180 pymacros import prints a stray line the first
    # time any test in the session imports it).
    out_path = tmp_path / "result.json"
    code = check_analog_lvs.main([*args, "--out", str(out_path)])
    return code, json.loads(out_path.read_text(encoding="utf-8"))

def run_err(args, capsys):
    """An exit-2 refusal goes to stdout, never to --out (checklib.cli_wrap).
    Only for runs that never import the GF180 pymacros, whose first import
    prints a stray line."""
    code = check_analog_lvs.main(args)
    return code, json.loads(capsys.readouterr().out)



def test_find_reference_netlist_refuses_with_neither_source(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    try:
        check_analog_lvs.find_reference_netlist(ws, "mirror")
        assert False, "expected CheckError"
    except Exception as exc:  # CheckError
        assert "no reference schematic" in str(exc)


def test_find_reference_netlist_reads_m8_cir(tmp_path):
    ws = tmp_path / "ws"
    (ws / "netlist").mkdir(parents=True)
    cir = ws / "netlist" / "mirror.cir"
    cir.write_text("* M8\n", encoding="utf-8")
    assert check_analog_lvs.find_reference_netlist(ws, "mirror") == cir


def test_find_reference_netlist_has_no_stand_in_fallback(tmp_path):
    ws = tmp_path / "ws"
    (ws / "layout_ref").mkdir(parents=True)
    (ws / "layout_ref" / "mirror.spice").write_text("* standin\n",
                                                    encoding="utf-8")
    with pytest.raises(Exception, match="no reference schematic"):
        check_analog_lvs.find_reference_netlist(ws, "mirror")


def test_reference_cell_is_spec_top(tmp_path):
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text("top: current_mirror\n",
                                           encoding="utf-8")
    text = ".subckt current_mirror a b\n.ends\n"
    assert check_analog_lvs.reference_cell(ws, "mirror", text) == "current_mirror"
    with pytest.raises(Exception, match="declares no .subckt"):
        check_analog_lvs.reference_cell(ws, "mirror", ".subckt other a\n.ends\n")


def test_sized_reference_applies_sizing_to_subckt_defaults_only():
    text = (".subckt r2r_dac bmsb blsb vout r_width=2e-6 r_length=1e-5 "
            "r2_length=1e-5\n"
            "xr bmsb vout rm1 r_width={r_width} r_length={r2_length}\n"
            ".ends r2r_dac\n")
    sized, applied = check_analog_lvs.sized_reference(
        text, "r2r_dac", {"r2_length": {"value": 2e-5}, "vbias": 1.0})
    assert applied == {"r2_length": 2e-5}
    assert "r2_length=2e-05\n" in sized.splitlines(keepends=True)[0]
    assert "r_length=1e-5 " in sized
    assert "r_length={r2_length}" in sized



def test_sized_reference_replaces_global_params():
    text = (".param w_tail=6e-6 w_in=4e-6\n"
            ".subckt cmp a b\n"
            "xm a b a a nfet w={w_tail} l=2.8e-7\n.ends\n")
    sized, applied = check_analog_lvs.sized_reference(
        text, "cmp", {"w_tail": {"value": 1e-5}})
    assert applied == {"w_tail": 1e-5}
    assert sized.splitlines()[0] == ".param w_tail=1e-05 w_in=4e-6"
    assert sized.count(".param") == 1


def test_sized_reference_defines_bench_supplied_params():
    # A library whose bench supplies the .param lines: netgen has no bench,
    # so every sizing name the subckt uses must be defined for it.
    text = ("* lib\n.subckt cmp a b\n"
            "xm a b a a nfet w={w_tail} l={l_tail}\n.ends\n")
    sized, applied = check_analog_lvs.sized_reference(
        text, "cmp", {"w_tail": {"value": 1e-5}, "l_tail": 2.8e-7,
                      "vbias": 1.0})
    assert applied == {"w_tail": 1e-5, "l_tail": 2.8e-7}
    lines = sized.splitlines()
    assert lines[1] == ".param w_tail=1e-05 l_tail=2.8e-07"
    assert lines[2].startswith(".subckt cmp")
    assert "vbias" not in sized

def test_unlaunchable_magic_is_a_refusal_not_a_stale_pass(unlaunchable_eda, tmp_path, capsys):
    ws = unlaunchable_eda
    code, out = run_err(["--workspace", str(ws)], capsys)
    assert code == 2, out
    assert out["status"] == "error"
    assert "failed to launch" in out["remediation"]
    assert not (ws / "log" / "lvs" / "blk.spice").exists()


@pytest.mark.slow
def test_clean_mirror_passes_real_lvs(tmp_path):
    ws = make_ws(tmp_path, "mirror")
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 0, out
    assert out["violations"] == []
    assert out["reference"] == "netlist/mirror.cir"
    assert out["reference_cell"] == "current_mirror"
    gate_result = gate.evaluate("lvs", _lvs_gate_row(), out)
    assert gate_result["status"] == "pass"


@pytest.mark.slow
def test_clean_r2r_dac_passes_real_lvs(tmp_path):
    ws = make_ws(tmp_path, "r2r_dac")
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 0, out
    assert out["reference"] == "netlist/r2r_dac.cir"
    assert out["sizing_applied"] == {"r_length": 1e-5, "r2_length": 1e-5}


@pytest.mark.slow
def test_swapped_net_fails_lvs(tmp_path):
    ws = make_ws(tmp_path, "mirror")
    plant = import_plant("mirror", "plant_lvs_swapped_net.py")
    plant(ws)
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "lvs_mismatch" in kinds
    gate_result = gate.evaluate("lvs", _lvs_gate_row(), out)
    assert gate_result["status"] == "fail"


@pytest.mark.slow
@pytest.mark.parametrize("block", ["mirror", "r2r_dac"])
def test_device_sized_differently_fails_lvs(tmp_path, block):
    ws = make_ws(tmp_path, block)
    plant = import_plant(block, "plant_lvs_sized_differently.py")
    plant(ws)
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 1, out
    assert {v["kind"] for v in out["violations"]} == {"lvs_mismatch"}
    assert "property error" in out["violations"][0]["msg"].lower()
    gate_result = gate.evaluate("lvs", _lvs_gate_row(), out)
    assert gate_result["status"] == "fail"
