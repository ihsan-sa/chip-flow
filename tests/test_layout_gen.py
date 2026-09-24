"""engine/scripts/layout_gen.py: run a block's generator (docs/design.md
1.3, 5, "### M9."). Fast tests cover block/generator resolution refusals;
slow tests actually build the real mirror/r2r_dac corpus generators through
gdsfactory (no magic/klayout/netgen subprocess - fast enough on its own,
but marked slow anyway since it exercises the same real-tool-adjacent path
check_analog_drc.py etc. depend on and the corpus fixtures are the same
ones faults.py's own slow run already covers)."""
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

import layout_gen  # noqa: E402
from checklib import CheckError  # noqa: E402

CORPUS = REPO / "corpus" / "ade"


def make_ws(tmp_path: Path, block: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "layout").mkdir(parents=True)
    (ws / "layout_ref").mkdir(parents=True)
    shutil.copy2(CORPUS / block / "layout" / f"gen_{block}.py",
                ws / "layout" / f"gen_{block}.py")
    ws.joinpath("state.json").write_text(
        json.dumps({"version": 3, "skill": "ade", "block": block}),
        encoding="utf-8")
    return ws


def test_block_of_reads_state_json(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ws.joinpath("state.json").write_text(
        json.dumps({"block": "mirror"}), encoding="utf-8")
    assert layout_gen.block_of(ws, None) == "mirror"


def test_block_of_explicit_overrides_state_json(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ws.joinpath("state.json").write_text(
        json.dumps({"block": "mirror"}), encoding="utf-8")
    assert layout_gen.block_of(ws, "r2r_dac") == "r2r_dac"


def test_block_of_refuses_with_no_state_and_no_override(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    try:
        layout_gen.block_of(ws, None)
        assert False, "expected CheckError"
    except CheckError as exc:
        assert "cannot tell which block" in str(exc)


def test_load_generator_refuses_missing_generator(tmp_path):
    ws = tmp_path / "ws"
    (ws / "layout").mkdir(parents=True)
    try:
        layout_gen.load_generator(ws, "mirror")
        assert False, "expected CheckError"
    except CheckError as exc:
        assert "no layout generator" in str(exc)


def test_load_generator_refuses_generator_with_no_generate_function(tmp_path):
    ws = tmp_path / "ws"
    (ws / "layout").mkdir(parents=True)
    (ws / "layout" / "gen_mirror.py").write_text("x = 1\n", encoding="utf-8")
    try:
        layout_gen.load_generator(ws, "mirror")
        assert False, "expected CheckError"
    except CheckError as exc:
        assert "has no generate()" in str(exc)


@pytest.mark.slow
@pytest.mark.parametrize("block,cell,pins", [
    # M8's own subckt names and pins (netlist/<block>.cir); the DAC's
    # ground is node 0, a named net but not a pin
    ("mirror", "current_mirror", {"iref_node", "iout", "vdd", "vss"}),
    ("r2r_dac", "r2r_dac", {"bmsb", "blsb", "vout"}),
])
def test_build_writes_gds_and_abstract(tmp_path, block, cell, pins):
    ws = make_ws(tmp_path, block)
    gds_path, topcell, abstract_path, abstract = layout_gen.build(ws)
    assert gds_path.is_file()
    assert topcell == cell
    assert abstract_path.is_file()
    assert {p["name"] for p in abstract["pins"]} == pins
    on_disk = json.loads(abstract_path.read_text(encoding="utf-8"))
    assert on_disk == abstract


@pytest.mark.slow
def test_run_cli_reports_pins(tmp_path):
    # --out, not capsys: the GF180 pymacros "cells" package prints a stray
    # "(22, 0)" line the first time anything in the process imports it
    # (proved empirically running this suite - a vendor module-level side
    # effect), which would land ahead of the JSON on stdout if this test
    # happens to run first in the session.
    ws = make_ws(tmp_path, "mirror")
    out_path = tmp_path / "result.json"
    code = layout_gen.main(["--workspace", str(ws), "--out", str(out_path)])
    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert code == 0, out
    assert out["status"] == "pass"
    assert set(out["pins"]) == {"iref_node", "iout", "vdd", "vss"}
