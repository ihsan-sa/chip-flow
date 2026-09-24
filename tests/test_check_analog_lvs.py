"""engine/scripts/check_analog_lvs.py: the ade `lvs` gate (docs/design.md
1.5, 5, "### M9."). Real magic extraction + netgen LVS against the corpus's
own layout_ref/ schematic (docs/spikes/glayout.md: "the spike's own LVS
only compared gLayout's output with itself... not a real schematic-vs-
layout LVS" - this one is)."""
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
    ws = tmp_path / "ws"
    (ws / "layout").mkdir(parents=True)
    (ws / "layout_ref").mkdir(parents=True)
    shutil.copy2(CORPUS / block / "layout" / f"gen_{block}.py",
                ws / "layout" / f"gen_{block}.py")
    for f in (CORPUS / block / "layout_ref").glob("*.spice"):
        shutil.copy2(f, ws / "layout_ref" / f.name)
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


def test_find_reference_netlist_refuses_with_neither_source(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    try:
        check_analog_lvs.find_reference_netlist(ws, "mirror")
        assert False, "expected CheckError"
    except Exception as exc:  # CheckError
        assert "no reference schematic" in str(exc)


def test_find_reference_netlist_prefers_canonical_netlist_dir(tmp_path):
    ws = tmp_path / "ws"
    (ws / "netlist").mkdir(parents=True)
    (ws / "layout_ref").mkdir(parents=True)
    canonical = ws / "netlist" / "mirror.spice"
    canonical.write_text("* canonical\n", encoding="utf-8")
    (ws / "layout_ref" / "mirror.spice").write_text("* standin\n", encoding="utf-8")
    found = check_analog_lvs.find_reference_netlist(ws, "mirror")
    assert found == canonical


@pytest.mark.slow
def test_clean_mirror_passes_real_lvs(tmp_path):
    ws = make_ws(tmp_path, "mirror")
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 0, out
    assert out["violations"] == []
    gate_result = gate.evaluate("lvs", _lvs_gate_row(), out)
    assert gate_result["status"] == "pass"


@pytest.mark.slow
def test_clean_r2r_dac_passes_real_lvs(tmp_path):
    ws = make_ws(tmp_path, "r2r_dac")
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 0, out


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
