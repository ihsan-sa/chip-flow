"""engine/scripts/check_drc.py: the drc gate (docs/design.md 1.5's drc row,
"### M4."). Fakes `eda magic` / `eda klayout` (never the real tools -
tests/smoke-harden.sh runs those) to prove the gate's failure
classification, plus direct unit tests of run_magic_drc/run_klayout_drc's
own output parsing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_drc  # noqa: E402
from checklib import CheckError  # noqa: E402


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nports: {}\ntt_pins: {}\n",
        encoding="utf-8")
    final = ws / "harden" / "runs" / "run" / "final"
    (final / "gds").mkdir(parents=True)
    (final / "gds" / "tt_um_counter8.gds").write_text("x", encoding="utf-8")
    return ws


def test_no_gds_refuses(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nports: {}\ntt_pins: {}\n",
        encoding="utf-8")
    code = check_drc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no hardened GDS" in out["error"]


def test_magic_no_count_line_refuses(tmp_path):
    gds = tmp_path / "x.gds"
    gds.write_text("x", encoding="utf-8")

    class FakeProc:
        returncode = 0
        stdout = "some unrelated magic banner text\n"
        stderr = ""

    import unittest.mock as mock
    with mock.patch.object(check_drc.subprocess, "run", return_value=FakeProc()):
        with pytest.raises(CheckError, match="never reported a violation count"):
            check_drc.run_magic_drc(gds, "top", tmp_path)


def test_magic_parses_nonzero_count(tmp_path):
    gds = tmp_path / "x.gds"
    gds.write_text("x", encoding="utf-8")

    class FakeProc:
        returncode = 0
        stdout = "[INFO]: COUNT: 3\n"
        stderr = ""

    import unittest.mock as mock
    with mock.patch.object(check_drc.subprocess, "run", return_value=FakeProc()):
        count = check_drc.run_magic_drc(gds, "top", tmp_path)
    assert count == 3


def test_klayout_no_report_file_refuses(tmp_path):
    gds = tmp_path / "x.gds"
    gds.write_text("x", encoding="utf-8")
    pdk_root = tmp_path / "pdks"
    deck = pdk_root / "gf180mcuD" / "libs.tech" / "klayout" / "tech" / "drc" / "gf180mcu.drc"
    deck.parent.mkdir(parents=True)
    deck.write_text("x", encoding="utf-8")

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "klayout crashed\n"

    import unittest.mock as mock
    with mock.patch.object(check_drc.subprocess, "run", return_value=FakeProc()):
        with pytest.raises(CheckError, match="produced no report database"):
            check_drc.run_klayout_drc(gds, "top", pdk_root, tmp_path)


def test_run_end_to_end_pass_when_both_tools_report_zero(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    monkeypatch.setattr(check_drc, "run_magic_drc", lambda gds, top, wd: 0)
    monkeypatch.setattr(check_drc, "run_klayout_drc", lambda gds, top, pdk, wd: 0)
    monkeypatch.setattr(check_drc, "_pdk_root", lambda: Path("/fake/toolchain/foss/pdks"))

    code = check_drc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"


def test_run_end_to_end_fails_on_nonzero_counts(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    monkeypatch.setattr(check_drc, "run_magic_drc", lambda gds, top, wd: 2)
    monkeypatch.setattr(check_drc, "run_klayout_drc", lambda gds, top, pdk, wd: 1)
    monkeypatch.setattr(check_drc, "_pdk_root", lambda: Path("/fake/toolchain/foss/pdks"))

    code = check_drc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["status"] == "violations"
    kinds = {v["kind"] for v in out["violations"]}
    assert kinds == {"magic_drc_violation", "klayout_drc_violation"}


def test_run_never_passes_final_dir_as_the_scratch_workdir(tmp_path, monkeypatch, capsys):
    # Regression: run_magic_drc/run_klayout_drc's scratch/report files used
    # to land in harden/runs/run/final/, the exact tree the "harden"
    # artifact-kind's dir_text hash recursively covers (invalidation.yaml) -
    # any write there staled every OTHER gate that also reads "harden".
    # run() must hand them a workdir under ws/log/, never final_dir.
    ws = make_ws(tmp_path)
    final_dir = ws / "harden" / "runs" / "run" / "final"
    seen = {}

    def fake_magic(gds, top, workdir):
        seen["magic"] = workdir
        return 0

    def fake_klayout(gds, top, pdk, workdir):
        seen["klayout"] = workdir
        return 0

    monkeypatch.setattr(check_drc, "run_magic_drc", fake_magic)
    monkeypatch.setattr(check_drc, "run_klayout_drc", fake_klayout)
    monkeypatch.setattr(check_drc, "_pdk_root", lambda: Path("/fake/toolchain/foss/pdks"))

    code = check_drc.main(["--workspace", str(ws)])
    assert code == 0, json.loads(capsys.readouterr().out)
    for tool, workdir in seen.items():
        assert final_dir not in workdir.parents and workdir != final_dir, \
            f"{tool}: workdir {workdir} is inside final_dir {final_dir}"
        assert (ws / "log") in workdir.parents or workdir == ws / "log", \
            f"{tool}: workdir {workdir} is not under ws/log/"
