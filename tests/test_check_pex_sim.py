"""engine/scripts/check_pex_sim.py: the ade `pex_sim` gate (docs/design.md
1.5, 5, "### M9."). Real magic parasitic extraction (every capacitor, and
extresist's wire resistance) then ngspice at typical corner against the
corpus's own layout_ref/ post-layout bench + .bounds.json."""
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

import check_pex_sim  # noqa: E402
import gate  # noqa: E402
import yaml as _yaml  # noqa: E402
from checklib import CheckError  # noqa: E402

CORPUS = REPO / "corpus" / "ade"
GATES_YAML = ENGINE / "reference" / "gates.yaml"


def _pex_sim_gate_row():
    rows = _yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]["ade"]
    return rows["pex_sim"]


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
    shutil.copy2(CORPUS / block / "spec.yaml", ws / "spec.yaml")
    ws.joinpath("state.json").write_text(
        json.dumps({"version": 3, "skill": "ade", "block": block}),
        encoding="utf-8")
    return ws


def test_find_bench_refuses_with_neither_source(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    try:
        check_pex_sim.find_bench(ws, "mirror")
        assert False, "expected CheckError"
    except CheckError as exc:
        assert "no pex_sim bench" in str(exc)


def test_find_bench_prefers_tb_dir(tmp_path):
    ws = tmp_path / "ws"
    (ws / "tb").mkdir(parents=True)
    (ws / "layout_ref").mkdir(parents=True)
    canon_cir = ws / "tb" / "mirror_pex_tb.cir"
    canon_bounds = ws / "tb" / "mirror_pex_tb.bounds.json"
    canon_cir.write_text("* canonical\n", encoding="utf-8")
    canon_bounds.write_text("{}", encoding="utf-8")
    (ws / "layout_ref" / "mirror_pex_tb.cir").write_text("* standin\n",
                                                         encoding="utf-8")
    (ws / "layout_ref" / "mirror_pex_tb.bounds.json").write_text("{}",
                                                                 encoding="utf-8")
    cir, bounds = check_pex_sim.find_bench(ws, "mirror")
    assert cir == canon_cir
    assert bounds == canon_bounds


def run_json(args, tmp_path):
    # --out, not capsys - see test_check_analog_drc.py's own run_json for
    # why (a vendor GF180 pymacros import prints a stray line the first
    # time any test in the session imports it).
    out_path = tmp_path / "result.json"
    code = check_pex_sim.main([*args, "--out", str(out_path)])
    return code, json.loads(out_path.read_text(encoding="utf-8"))

def run_err(args, capsys):
    """An exit-2 refusal goes to stdout, never to --out (checklib.cli_wrap).
    Only for runs that never import the GF180 pymacros, whose first import
    prints a stray line."""
    code = check_pex_sim.main(args)
    return code, json.loads(capsys.readouterr().out)



def test_reorder_pins_puts_extracted_pins_in_reference_order():
    text = (".subckt current_mirror iref_node iout vss vdd\n"
            "X0 iout iref_node vss vss nfet_03v3 w=8u l=0.5u\n.ends\n")
    out = check_pex_sim.reorder_pins(
        text, "current_mirror", ["iref_node", "iout", "vdd", "vss"])
    assert out.splitlines()[0] == ".subckt current_mirror iref_node iout vdd vss"
    assert out.splitlines()[1] == text.splitlines()[1]


def test_reorder_pins_refuses_a_different_pin_set():
    text = ".subckt blk a b\n.ends\n"
    with pytest.raises(CheckError, match="pins"):
        check_pex_sim.reorder_pins(text, "blk", ["a", "c"])


def test_pins_of_skips_subckt_parameters():
    text = ".subckt r2r_dac bmsb blsb vout r_width=2e-6\n.ends\n"
    assert check_pex_sim.pins_of(text, "r2r_dac") == ["bmsb", "blsb", "vout"]


def test_unlaunchable_magic_is_a_refusal(unlaunchable_eda, tmp_path, capsys):
    code, out = run_err(["--workspace", str(unlaunchable_eda)], capsys)
    assert code == 2, out
    assert out["status"] == "error"
    assert "failed to launch" in out["remediation"]


def test_netlist_with_no_parasitics_is_a_refusal(unlaunchable_eda, tmp_path,
                                                 monkeypatch, capsys):
    # what the review found: `ext2spice lvs` after the thresholds left the
    # "parasitic" netlist identical to the LVS one, devices only
    import layoutlib
    ws = unlaunchable_eda

    def lvs_only(work_dir, gds, cell, parasitics):
        p = work_dir / f"{cell}.pex.spice"
        p.write_text(".subckt blk a b\nX0 a b rm1 r_width=2u r_length=10u\n"
                     ".ends\n", encoding="utf-8")
        return p, ""

    monkeypatch.setattr(layoutlib, "run_magic_extract", lvs_only)
    code, out = run_err(["--workspace", str(ws)], capsys)
    assert code == 2, out
    assert "no R and no C" in out["remediation"]


@pytest.mark.slow
def test_clean_mirror_pex_sim_passes(tmp_path):
    ws = make_ws(tmp_path, "mirror")
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 0, out
    assert out["violations"] == []
    assert out["parasitics"]["r"] > 0 and out["parasitics"]["c"] > 0
    assert set(out["measured"]) == {"v(iout)", "f3db"}
    gate_result = gate.evaluate("pex_sim", _pex_sim_gate_row(), out)
    assert gate_result["status"] == "pass"


@pytest.mark.slow
def test_clean_r2r_dac_pex_sim_passes(tmp_path):
    ws = make_ws(tmp_path, "r2r_dac")
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 0, out
    assert out["parasitics"]["c"] > 0
    m = out["measured"]
    # the three codes of a 1:1 ladder, the rung's current sizing
    assert abs(m["vout_lsb"] - 0.66) < 0.02
    assert abs(m["vout_msb"] - 1.98) < 0.02
    assert abs(m["vout_both"] - 2.64) < 0.02


@pytest.mark.slow
def test_long_output_line_fails_pex_sim(tmp_path):
    ws = make_ws(tmp_path, "mirror")
    path = CORPUS / "mirror" / "faults" / "plant_pex_long_output_line.py"
    spec = importlib.util.spec_from_file_location("plant_pex_long", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.plant(ws)
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 1, out
    bad = {v["refs"][0] for v in out["violations"]}
    assert bad == {"f3db"}
    gate_result = gate.evaluate("pex_sim", _pex_sim_gate_row(), out)
    assert gate_result["status"] == "fail"
