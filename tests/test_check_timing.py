"""engine/scripts/check_timing.py: the timing gate (docs/design.md 1.5's
timing row, "### M4."). Fakes `eda sta`'s own output (never the real
OpenSTA - tests/smoke-harden.sh runs that) to prove the gate's failure
classification: a launcher that never produces a parseable "worst slack"
line is a refusal, a negative slack is a finding, never a silent pass.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_timing  # noqa: E402
import ttlib  # noqa: E402


def make_ws_with_harden(tmp_path: Path, corners=("nom_tt_025C_3v30",)) -> Path:
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\n"
        "ports: {clk: {dir: input, width: 1}}\n"
        "tt_pins: {clk: clk}\n", encoding="utf-8")
    final = ws / "harden" / "runs" / "run" / "final"
    top = "tt_um_counter8"
    for corner in corners:
        (final / "lib" / corner).mkdir(parents=True)
        (final / "nl").mkdir(parents=True, exist_ok=True)
        (final / "nl" / f"{top}.nl.v").write_text("x", encoding="utf-8")
        (final / "sdc").mkdir(parents=True, exist_ok=True)
        (final / "sdc" / f"{top}.sdc").write_text("x", encoding="utf-8")
        bucket = corner.split("_", 1)[0]
        (final / "spef" / bucket).mkdir(parents=True, exist_ok=True)
        (final / "spef" / bucket / f"{top}.{bucket}.spef").write_text("x", encoding="utf-8")
    return ws


def _patch_common(monkeypatch, tmp_path):
    # a fake, always-existing liberty file - the real one lives in the PDK,
    # outside anything this test controls or needs.
    fake_lib = tmp_path / "fake.lib"
    fake_lib.write_text("x", encoding="utf-8")
    monkeypatch.setattr(ttlib, "stdcell_liberty_path", lambda corner, pdk_root: fake_lib)

    def fake_pdk_root(*a, **kw):
        return Path("/fake/toolchain/foss/pdks")
    monkeypatch.setattr(check_timing, "_pdk_root", fake_pdk_root)


def test_no_harden_output_refuses(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nports: {}\ntt_pins: {}\n",
        encoding="utf-8")
    code = check_timing.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "has not produced" in out["error"]


def test_sta_with_no_parseable_slack_refuses(tmp_path, monkeypatch, capsys):
    ws = make_ws_with_harden(tmp_path)
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="Error: something broke\n", stderr="")
    monkeypatch.setattr(check_timing.subprocess, "run", fake_run)

    code = check_timing.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no parseable worst-slack" in out["error"]


def test_negative_setup_slack_is_a_violation(tmp_path, monkeypatch, capsys):
    ws = make_ws_with_harden(tmp_path, corners=("max_ss_125C_3v00",))
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="worst slack max -0.06\nworst slack min 2.23\n", stderr="")
    monkeypatch.setattr(check_timing.subprocess, "run", fake_run)

    code = check_timing.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["status"] == "violations"
    kinds = {v["kind"] for v in out["violations"]}
    assert "setup_violation" in kinds


def test_all_corners_positive_slack_pass(tmp_path, monkeypatch, capsys):
    ws = make_ws_with_harden(tmp_path, corners=("nom_tt_025C_3v30", "max_ss_125C_3v00"))
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="worst slack max 7.97\nworst slack min 2.23\n", stderr="")
    monkeypatch.setattr(check_timing.subprocess, "run", fake_run)

    code = check_timing.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert len(out["corners"]) == 2

    # Regression: the per-corner .sta_<corner>.tcl scratch file used to be
    # written straight into harden/runs/run/final/, the exact tree the
    # "harden" artifact-kind's dir_text hash recursively covers
    # (invalidation.yaml) - a write there staled every OTHER gate that also
    # reads "harden". It must land under ws/log/timing_work/ instead, and
    # final/ must stay exactly what harden itself produced (nothing new).
    final_dir = ws / "harden" / "runs" / "run" / "final"
    assert not list(final_dir.glob(".sta_*")), \
        "check_timing wrote a scratch file into final_dir"
    work_dir = ws / "log" / "timing_work"
    assert len(list(work_dir.glob(".sta_*.tcl"))) == 2, \
        "expected one .sta_<corner>.tcl per corner under ws/log/timing_work/"
