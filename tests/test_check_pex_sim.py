"""engine/scripts/check_pex_sim.py: the ade `pex_sim` gate (docs/design.md
1.5, 5, "### M9."). Real magic parasitic extraction (extract + extresist)
then ngspice at typical corner against the corpus's own layout_ref/ bench
+ .bounds.json."""
from __future__ import annotations

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
    ws = tmp_path / "ws"
    (ws / "layout").mkdir(parents=True)
    (ws / "layout_ref").mkdir(parents=True)
    shutil.copy2(CORPUS / block / "layout" / f"gen_{block}.py",
                ws / "layout" / f"gen_{block}.py")
    for f in (CORPUS / block / "layout_ref").iterdir():
        if f.is_file():
            shutil.copy2(f, ws / "layout_ref" / f.name)
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


@pytest.mark.slow
def test_clean_mirror_pex_sim_passes(tmp_path):
    ws = make_ws(tmp_path, "mirror")
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 0, out
    assert out["violations"] == []
    assert "v(iref)" in out["measured"]
    assert "v(iout)" in out["measured"]
    gate_result = gate.evaluate("pex_sim", _pex_sim_gate_row(), out)
    assert gate_result["status"] == "pass"


@pytest.mark.slow
def test_clean_r2r_dac_pex_sim_passes_and_divides_by_two(tmp_path):
    ws = make_ws(tmp_path, "r2r_dac")
    code, out = run_json(["--workspace", str(ws)], tmp_path)
    assert code == 0, out
    vin = out["measured"]["v(vin)"]
    vout = out["measured"]["v(vout)"]
    assert abs(vout - vin / 2) < 0.05
