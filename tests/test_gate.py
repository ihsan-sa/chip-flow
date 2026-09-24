"""engine/scripts/gate.py: generic tool dispatch (check_<tool>.py, dynamically
imported - docs/design.md 1.5), evaluate(), self-recording into state.json,
and the M1 stub contract (a gate whose tool is not built is exit 2, never a
pass)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import gate  # noqa: E402
import state as state_mod  # noqa: E402

FAKE_CHECK = '''
from pathlib import Path

def run(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    marker = Path(args.workspace) / "FAIL"
    violations = []
    if marker.exists():
        violations = [{"check": "fake", "severity": "error", "file": "x",
                       "module": None, "kind": "bad", "refs": [],
                       "msg": "boom", "source": "check_fakegate"}]
    payload = {"script": "check_fakegate",
              "status": "violations" if violations else "pass",
              "counts": {"total": len(violations)}, "violations": violations,
              "report_schema": 1, "checker_version": 1,
              "generated_at": "2026-01-01T00:00:00+00:00",
              "input": str(args.workspace), "input_digest": None}
    return payload, args.out
'''

FAKE_GATES_YAML = '''
version: 1
gates:
  vde:
    lint:
      phase: P4
      tool: fakegate
      fail_severities: [error]
      max_count: 0
'''


def make_ws(tmp_path: Path, skill="vde", block="counter8") -> Path:
    ws = tmp_path / "ws"
    state_mod.State.init(ws, skill, block)
    return ws


def make_checks_dir(tmp_path: Path) -> Path:
    d = tmp_path / "checks"
    d.mkdir()
    (d / "check_fakegate.py").write_text(FAKE_CHECK, encoding="utf-8")
    return d


def make_gates_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "gates.yaml"
    p.write_text(FAKE_GATES_YAML, encoding="utf-8")
    return p


# --------------------------------------------------------------- evaluate

def test_evaluate_pass_and_fail_thresholds():
    g = {"fail_severities": ["error"], "max_count": 0, "phase": "P4",
        "tool": "lint"}
    report = {"violations": [], "counts": {"total": 0}}
    assert gate.evaluate("lint", g, report)["status"] == "pass"
    report2 = {"violations": [{"severity": "error"}], "counts": {"total": 1}}
    r = gate.evaluate("lint", g, report2)
    assert r["status"] == "fail" and r["failing_count"] == 1
    report3 = {"violations": [{"severity": "warning"}], "counts": {"total": 1}}
    assert gate.evaluate("lint", g, report3)["status"] == "pass"


# ------------------------------------------------------------------ stub

def test_stub_gate_is_always_exit_2(tmp_path, capsys):
    ws = make_ws(tmp_path)
    code = gate.main(["--gate", "lint", "--workspace", str(ws)])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert "not built" in out["error"]


# --------------------------------------------------------- real dispatch

def test_pass_records_into_state(tmp_path, capsys):
    ws = make_ws(tmp_path)
    checks_dir = make_checks_dir(tmp_path)
    gates_yaml = make_gates_yaml(tmp_path)
    code = gate.main(["--gate", "lint", "--workspace", str(ws),
                      "--gates", str(gates_yaml), "--checks-dir", str(checks_dir)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pass"
    assert out["record_result"]["recorded"] is True
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert data["gates"]["lint"]["status"] == "pass"
    assert "rtl" in data["gates"]["lint"]["last"]["inputs"]


def test_fail_records_and_exits_1(tmp_path, capsys):
    ws = make_ws(tmp_path)
    (ws / "FAIL").write_text("x", encoding="utf-8")
    checks_dir = make_checks_dir(tmp_path)
    gates_yaml = make_gates_yaml(tmp_path)
    code = gate.main(["--gate", "lint", "--workspace", str(ws),
                      "--gates", str(gates_yaml), "--checks-dir", str(checks_dir)])
    assert code == 1
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert data["gates"]["lint"]["status"] == "fail"
    assert data["gates"]["lint"]["attempts"] == 1


def test_no_record_flag_skips_state(tmp_path, capsys):
    ws = make_ws(tmp_path)
    checks_dir = make_checks_dir(tmp_path)
    gates_yaml = make_gates_yaml(tmp_path)
    code = gate.main(["--gate", "lint", "--workspace", str(ws),
                      "--gates", str(gates_yaml), "--checks-dir", str(checks_dir),
                      "--no-record"])
    assert code == 0
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert data["gates"] == {}


def test_unknown_gate_for_skill_errors(tmp_path, capsys):
    ws = make_ws(tmp_path)
    code = gate.main(["--gate", "not-a-gate", "--workspace", str(ws)])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert "unknown gate" in out["error"]


def test_skill_override_used_without_workspace(tmp_path, capsys):
    checks_dir = make_checks_dir(tmp_path)
    gates_yaml = make_gates_yaml(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    code = gate.main(["--gate", "lint", "--workspace", str(scratch),
                      "--skill", "vde", "--gates", str(gates_yaml),
                      "--checks-dir", str(checks_dir), "--no-record"])
    assert code == 0    # no state.json above scratch -> nothing to record,
                        # but the gate itself still runs and passes


def test_missing_workspace_and_skill_refuses(tmp_path, capsys):
    code = gate.main(["--gate", "lint"])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert "cannot tell which skill" in out["error"]


# --------------------------------------------------------------- --report

def test_report_mode_validates_schema(tmp_path, capsys):
    ws = make_ws(tmp_path)
    gates_yaml = make_gates_yaml(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    code = gate.main(["--gate", "lint", "--workspace", str(ws),
                      "--gates", str(gates_yaml), "--report", str(bad)])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert "--report refused" in out["error"]


def test_report_mode_accepts_a_well_formed_report(tmp_path, capsys):
    ws = make_ws(tmp_path)
    gates_yaml = make_gates_yaml(tmp_path)
    import datetime
    good = {"script": "check_fakegate", "status": "pass",
           "report_schema": 1, "violations": [],
           "generated_at": datetime.datetime.now(datetime.timezone.utc)
           .isoformat(timespec="seconds"), "counts": {"total": 0}}
    rp = tmp_path / "good.json"
    rp.write_text(json.dumps(good), encoding="utf-8")
    code = gate.main(["--gate", "lint", "--workspace", str(ws),
                      "--gates", str(gates_yaml), "--report", str(rp)])
    assert code == 0
