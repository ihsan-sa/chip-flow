"""engine/scripts/check_lvs.py: the lvs gate (docs/design.md 1.5's lvs row,
"### M4."). Fakes `eda netgen` (never the real tool - tests/smoke-harden.sh
runs that) to prove the gate's failure classification: a comparison that
never reaches a decidable verdict is a refusal, a real mismatch is a
finding, never a silent pass.
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

import check_lvs  # noqa: E402


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nports: {}\ntt_pins: {}\n",
        encoding="utf-8")
    final = ws / "harden" / "runs" / "run" / "final"
    (final / "spice").mkdir(parents=True)
    (final / "spice" / "tt_um_counter8.spice").write_text("x", encoding="utf-8")
    (final / "pnl").mkdir(parents=True)
    (final / "pnl" / "tt_um_counter8.pnl.v").write_text("x", encoding="utf-8")
    return ws


def _patch_common(monkeypatch, tmp_path):
    monkeypatch.setattr(check_lvs, "_pdk_root",
                        lambda: Path("/fake/toolchain/foss/pdks"))
    setup_tcl = tmp_path / "setup.tcl"
    setup_tcl.write_text("x", encoding="utf-8")
    monkeypatch.setattr(check_lvs, "_netgen_setup_tcl", lambda pdk_root: setup_tcl)
    models = []
    for i in range(3):
        m = tmp_path / f"model{i}.spice"
        m.write_text("x", encoding="utf-8")
        models.append(m)
    monkeypatch.setattr(check_lvs, "PDK_SPICE_MODELS",
                        tuple(str(m.relative_to(tmp_path)) for m in models))
    # PDK_SPICE_MODELS are joined against pdk_root/PDK_NAME normally; patch
    # that join target to tmp_path directly so the fake model files resolve.
    import ttlib
    monkeypatch.setattr(ttlib, "PDK_NAME", ".")
    monkeypatch.setattr(check_lvs, "_pdk_root", lambda: tmp_path)


def test_no_extracted_netlist_refuses(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nports: {}\ntt_pins: {}\n",
        encoding="utf-8")
    code = check_lvs.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "has the harden gate run" in out["error"]


def test_no_decidable_verdict_refuses(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="netgen exploded\n", stderr="")
    monkeypatch.setattr(check_lvs.subprocess, "run", fake_run)

    code = check_lvs.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no decidable LVS verdict" in out["error"]


def test_mismatch_is_a_violation(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="Final result: Netlists do not match.\n", stderr="")
    monkeypatch.setattr(check_lvs.subprocess, "run", fake_run)

    code = check_lvs.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["status"] == "violations"
    assert out["violations"][0]["kind"] == "netlist_mismatch"


def test_match_passes(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="Final result: Circuits match uniquely.\n", stderr="")
    monkeypatch.setattr(check_lvs.subprocess, "run", fake_run)

    code = check_lvs.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["matched"] is True

    # Regression: report_path/lvs_script used to be written straight into
    # harden/runs/run/final/, the exact tree the "harden" artifact-kind's
    # dir_text hash recursively covers (invalidation.yaml) - a write there
    # staled every OTHER gate that also reads "harden". They must land under
    # ws/log/lvs_work/ instead, and final/ must stay exactly what harden
    # itself produced (spice/, pnl/ only, from make_ws above).
    final_dir = ws / "harden" / "runs" / "run" / "final"
    assert sorted(p.name for p in final_dir.iterdir()) == ["pnl", "spice"], \
        "check_lvs wrote a scratch/report file into final_dir"
    work_dir = ws / "log" / "lvs_work"
    assert any(work_dir.glob(".lvs_check_*")), \
        "expected check_lvs's scratch files under ws/log/lvs_work/"
