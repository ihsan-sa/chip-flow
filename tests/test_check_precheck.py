"""engine/scripts/check_precheck.py: the precheck gate (docs/design.md
1.5's precheck row, "### M4."). Fakes the vendored precheck.py's own
subprocess invocation (never the real tool - tests/smoke-harden.sh runs
that) to prove the gate's failure classification, including the fault
gates.yaml names for this gate ("a wrong top module name in info.yaml")
surfacing as a `violations` finding rather than an uncaught crash.
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

import check_precheck  # noqa: E402
import ttlib  # noqa: E402


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nports: {}\ntt_pins: {}\n",
        encoding="utf-8")
    final = ws / "harden" / "runs" / "run" / "final"
    (final / "gds").mkdir(parents=True)
    (final / "gds" / "tt_um_counter8.gds").write_text("x", encoding="utf-8")
    (final / "lef").mkdir(parents=True)
    (final / "lef" / "tt_um_counter8.lef").write_text("x", encoding="utf-8")
    (final / "pnl").mkdir(parents=True)
    (final / "pnl" / "tt_um_counter8.pnl.v").write_text("x", encoding="utf-8")
    return ws


def _patch_common(monkeypatch, tmp_path):
    monkeypatch.setattr(check_precheck, "_pdk_root",
                        lambda: Path("/fake/toolchain/foss/pdks"))
    pydeps = tmp_path / "pydeps"
    pydeps.mkdir()
    monkeypatch.setattr(ttlib, "ensure_precheck_deps", lambda eda_bin: pydeps)
    shim = tmp_path / "shim"
    shim.mkdir()
    monkeypatch.setattr(ttlib, "yowasp_yosys_shim_dir", lambda eda_bin, cache: shim)


def test_no_harden_output_refuses(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nports: {}\ntt_pins: {}\n",
        encoding="utf-8")
    code = check_precheck.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "has not run yet" in out["error"]


def test_no_results_xml_is_an_error(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="import error")
    monkeypatch.setattr(check_precheck.subprocess, "run", fake_run)

    code = check_precheck.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "wrote no" in out["error"]


def test_wrong_top_module_name_is_a_violation_not_a_crash(tmp_path, monkeypatch, capsys):
    # the fault gates.yaml names for this gate: a mismatched top module
    # name surfaces as a failed <testcase>, same shape a real precheck.py
    # assertion failure or a KLayout-checks top-name mismatch takes.
    ws = make_ws(tmp_path)
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        reports = Path(cmd[2]).parent / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "results.xml").write_text(
            '<?xml version="1.0"?><testsuites><testsuite>'
            '<testcase name="KLayout Checks">'
            '<error message="Top macro name mismatch: expected tt_um_wrong, '
            'got tt_um_counter8"/></testcase></testsuite></testsuites>',
            encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
    monkeypatch.setattr(check_precheck.subprocess, "run", fake_run)

    code = check_precheck.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["status"] == "violations"
    assert out["violations"][0]["kind"] == "precheck_failed"
    assert "Top macro name mismatch" in out["violations"][0]["msg"]


def test_all_checks_pass(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        reports = Path(cmd[2]).parent / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "results.xml").write_text(
            '<?xml version="1.0"?><testsuites><testsuite>'
            '<testcase name="Layer check"/>'
            '<testcase name="Cell name check"/>'
            '</testsuite></testsuites>', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(check_precheck.subprocess, "run", fake_run)

    code = check_precheck.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["checks_run"] == 2


def _passing_run(cmd, **kwargs):
    reports = Path(cmd[2]).parent / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "results.xml").write_text(
        '<?xml version="1.0"?><testsuites><testsuite>'
        '<testcase name="Layer check"/></testsuite></testsuites>',
        encoding="utf-8")
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _stamped_matches_first_input(tmp_path, monkeypatch, capsys, skill):
    """The report's input_digest is what state.py record_gate compares with
    the gate's first input in invalidation.yaml; a mismatch refuses to
    record a real pass."""
    import statelib
    block = make_ws(tmp_path)
    ws = block
    if skill == "msde":
        ws = tmp_path / "msde"
        ws.mkdir()
        block.rename(ws / "top")
    (ws / "state.json").write_text(json.dumps({"skill": skill}),
                                   encoding="utf-8")
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(check_precheck.subprocess, "run", _passing_run)

    code = check_precheck.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    imap = statelib.load_map()
    first = imap["gate_inputs"][skill]["precheck"][0]
    rel, cur = statelib.hash_kind(ws, first, imap)
    assert cur is not None
    assert out["input_digest"] == cur, (skill, first, rel, out["input"])


def test_msde_stamps_top_gds_the_state_rehashes(tmp_path, monkeypatch, capsys):
    # regression: msde precheck stamped top/harden, record_gate re-hashed
    # top_gds, and a real pass was refused as a stale artifact
    _stamped_matches_first_input(tmp_path, monkeypatch, capsys, "msde")


def test_vde_stamps_harden_dir_the_state_rehashes(tmp_path, monkeypatch, capsys):
    _stamped_matches_first_input(tmp_path, monkeypatch, capsys, "vde")

