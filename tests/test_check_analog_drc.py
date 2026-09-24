"""engine/scripts/check_analog_drc.py: the ade `drc` gate (docs/design.md
1.5, 5, "### M9."). Slow tests run the REAL klayout GF180 signoff deck,
metal and via rules included, against the real mirror/r2r_dac corpus
generators (docs/spikes/glayout.md's own "klayout's GF180 deck is the DRC
that counts" - magic is deliberately not run here, see the module's own
docstring)."""
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

import check_analog_drc  # noqa: E402
import gate  # noqa: E402
import yaml as _yaml  # noqa: E402

CORPUS = REPO / "corpus" / "ade"
GATES_YAML = ENGINE / "reference" / "gates.yaml"


def _drc_gate_row():
    rows = _yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]["ade"]
    return rows["drc"]


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
    # --out, not capsys: the GF180 pymacros "cells" package prints a stray
    # "(22, 0)" line the FIRST time anything imports it in a given process
    # (proved empirically - a vendor module-level side effect, not this
    # repo's own code) - capsys.readouterr().out would then be a real
    # print line followed by JSON, not JSON alone, and only on whichever
    # test happens to run first in the session.
    out_path = tmp_path / "result.json"
    code = check_analog_drc.main([*args, "--out", str(out_path)])
    return code, json.loads(out_path.read_text(encoding="utf-8"))

def run_err(args, capsys):
    """An exit-2 refusal goes to stdout, never to --out (checklib.cli_wrap).
    Only for runs that never import the GF180 pymacros, whose first import
    prints a stray line."""
    code = check_analog_drc.main(args)
    return code, json.loads(capsys.readouterr().out)



def test_run_refuses_when_no_generator(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "layout").mkdir(parents=True)
    ws.joinpath("state.json").write_text(
        json.dumps({"block": "mirror"}), encoding="utf-8")
    code = check_analog_drc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no layout generator" in out["remediation"]


@pytest.mark.slow
def test_clean_mirror_passes_real_klayout_drc(tmp_path):
    ws = make_ws(tmp_path, "mirror")
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["violations"] == []
    gate_result = gate.evaluate("drc", _drc_gate_row(), out)
    assert gate_result["status"] == "pass"


@pytest.mark.slow
def test_clean_r2r_dac_passes_real_klayout_drc(tmp_path):
    ws = make_ws(tmp_path, "r2r_dac")
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 0, out
    assert out["status"] == "pass"


@pytest.mark.slow
def test_missing_guard_ring_fails_drc_with_df14(tmp_path):
    ws = make_ws(tmp_path, "mirror")
    plant = import_plant("mirror", "plant_drc_missing_guard_ring.py")
    plant(ws)
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "DF.14_LV" in kinds
    gate_result = gate.evaluate("drc", _drc_gate_row(), out)
    assert gate_result["status"] == "fail"


def test_unlaunchable_klayout_is_a_refusal_not_a_stale_pass(unlaunchable_eda, tmp_path, capsys):
    ws = unlaunchable_eda
    code, out = run_err(["--workspace", str(ws)], capsys)
    assert code == 2, out
    assert out["status"] == "error"
    assert "failed to launch" in out["remediation"]
    # the clean report an earlier run left is gone, not read back as a pass
    assert not (ws / "log" / "analog_drc.lyrdb").exists()


def test_drc_keeps_metal_and_via_rules_on():
    # the review's finding: "-beol" dropped metal.rb, via.rb and
    # guard_ring.rb. Only chip-level density and antenna may be off.
    import layoutlib
    tokens = layoutlib.DRC_DECKS.split(",")
    assert tokens[0] == "all"
    assert sorted(t for t in tokens if t.startswith("-")) == ["-antenna", "-density"]


@pytest.mark.slow
def test_metal_spacing_fault_fails_drc_with_m1_2a(tmp_path):
    ws = make_ws(tmp_path, "mirror")
    plant = import_plant("mirror", "plant_drc_metal_spacing.py")
    plant(ws)
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert kinds == {"M1.2a"}
    gate_result = gate.evaluate("drc", _drc_gate_row(), out)
    assert gate_result["status"] == "fail"


@pytest.mark.slow
def test_offgrid_shape_fails_drc(tmp_path):
    ws = make_ws(tmp_path, "r2r_dac")
    plant = import_plant("r2r_dac", "plant_drc_offgrid.py")
    plant(ws)
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert any("OFFGRID" in k for k in kinds)
