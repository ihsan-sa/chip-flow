"""engine/scripts/gate.py: generic tool dispatch (check_<tool>.py, dynamically
imported - docs/design.md 1.5), evaluate(), self-recording into state.json,
and the M1 stub contract (a gate whose tool is not built is exit 2, never a
pass)."""
from __future__ import annotations

import json
import sys
import threading
import time
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

# A check that ran but has nothing to report as pass/violations - a tool
# that decided its inputs don't apply here, say. gate.py must treat this as
# a refusal (exit 2), never as a pass: "skipped" is not in {pass,
# violations}, so evaluate() must never see it (gate.evaluate: "126."
# below).
SKIPPED_CHECK = '''
def run(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    payload = {"script": "check_fakegate", "status": "skipped",
              "counts": {"total": 0}, "violations": [],
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


def make_checks_dir(tmp_path: Path, body: str = FAKE_CHECK) -> Path:
    d = tmp_path / "checks"
    d.mkdir(exist_ok=True)
    (d / "check_fakegate.py").write_text(body, encoding="utf-8")
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


def test_evaluate_keeps_full_violations_list_not_just_failing():
    """M5 fix (docs/design.md, commit '10431ee gate.py/fix_dispatch: keep
    every severity, not just failing'): `failing`/`failing_count` still key
    pass/fail off fail_severities alone, but `violations` must carry EVERY
    severity the check reported. fix_dispatch.py reads `violations` in
    preference to `failing` precisely because most `mutate` survivor
    findings are severity info (gates.yaml's fail_severities is [error]) -
    a gate result that dropped back to `failing`-only would starve
    fix_dispatch of most of what a fixer actually needs."""
    g = {"fail_severities": ["error"], "max_count": 0, "phase": "P4",
        "tool": "mutate"}
    report = {"violations": [
        {"severity": "error", "kind": "kill_rate_below_threshold"},
        {"severity": "info", "kind": "survivor_reset_removed"},
        {"severity": "info", "kind": "survivor_other"},
    ], "counts": {"total": 3}}
    r = gate.evaluate("mutate", g, report)
    assert r["status"] == "fail"
    assert r["failing_count"] == 1
    assert len(r["failing"]) == 1
    assert len(r["violations"]) == 3   # every severity, not just failing
    assert {v["kind"] for v in r["violations"]} == {
        "kill_rate_below_threshold", "survivor_reset_removed",
        "survivor_other"}


def test_evaluate_surfaces_check_specific_facts():
    # a check script's own extra facts (mutate's kill_rate/survivors_by_
    # class, sim's tests_run, lint's top) must reach the gate's own result,
    # not be dropped along with the rest of the report envelope - docs/
    # design.md "### M2.": "gate.py --gate mutate ... reports a kill rate
    # and survivors by class".
    g = {"fail_severities": ["error"], "max_count": 0, "phase": "P4",
        "tool": "mutate"}
    report = {"violations": [], "counts": {"total": 0}, "status": "pass",
             "script": "check_mutate", "report_schema": 1, "input": "x",
             "kill_rate": 0.95, "survivors_by_class": {}, "wall_s": 12.3}
    r = gate.evaluate("mutate", g, report)
    assert r["facts"] == {"kill_rate": 0.95, "survivors_by_class": {},
                          "wall_s": 12.3}


def test_evaluate_no_facts_key_when_report_has_no_extras():
    g = {"fail_severities": ["error"], "max_count": 0}
    report = {"violations": [], "counts": {"total": 0}}
    assert "facts" not in gate.evaluate("lint", g, report)


# ------------------------------------------------------------------ stub

def test_stub_gate_is_always_exit_2(tmp_path, capsys):
    # Every /vde gate is a real, built check now (M1-M4), and M9 built
    # `ade`'s drc. `msde`'s top_drc is still a stub until M10's second half,
    # so it is what now proves this row's own point: a gate whose tool is
    # not built is exit 2, never a pass.
    ws = make_ws(tmp_path, skill="msde", block="sensor_counted")
    code = gate.main(["--gate", "top_drc", "--workspace", str(ws)])
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


def test_skipped_status_is_refused_not_a_pass(tmp_path, capsys):
    ws = make_ws(tmp_path)
    checks_dir = make_checks_dir(tmp_path, SKIPPED_CHECK)
    gates_yaml = make_gates_yaml(tmp_path)
    code = gate.main(["--gate", "lint", "--workspace", str(ws),
                      "--gates", str(gates_yaml), "--checks-dir", str(checks_dir)])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert "skipped" in out["error"]
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert data["gates"] == {}   # never recorded as any kind of result


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


# ------------------------------------------------------- concurrent writers

def test_record_gate_result_concurrent_writers_do_not_race(tmp_path, monkeypatch):
    """Two jobs recording into the SAME workspace at once (jobs.py: two
    detached gates finishing close together) must both land, not have the
    second lose a StaleWriteError to the first. record_gate_result used to
    call State.load()/record_gate()/save() with no lock spanning the three -
    only save() itself took the writer lock, around just the write - so two
    callers could both load the same bytes, both mutate, and have the loser's
    save() see a file that changed since ITS load and refuse (exactly
    state.py's own CLI already avoids by holding one lock across load ->
    mutate -> save, docs at state.py's module docstring "Writer safety").

    A patched State.load sleeps AFTER doing the real read, with no
    barrier/lock of its own: two threads started together race straight
    into that window. Against the unfixed code (lock only inside save())
    both loads land in the open window and one thread's save() raises
    StaleWriteError. Against the fix (the whole load -> mutate -> save span
    under one writer_lock) the second thread blocks for the lock before it
    can even call load, so it always reloads fresh and this passes."""
    ws = make_ws(tmp_path)
    gate_row = {"phase": "P4"}  # record_gate_result only reads .get("phase")

    orig_load = state_mod.State.load

    def slow_load(path):
        st = orig_load(path)
        time.sleep(0.2)
        return st

    monkeypatch.setattr(state_mod.State, "load", staticmethod(slow_load))

    outcomes: list[dict] = [None, None]  # type: ignore[list-item]

    def writer(i):
        result = {"status": "pass", "failing_count": 0, "counts": {"total": 0}}
        outcomes[i] = gate.record_gate_result("vde", "lint", gate_row, result, ws)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "writer thread hung"

    for outcome in outcomes:
        assert outcome is not None
        assert outcome["ok"] is True, outcome
        assert outcome["recorded"] is True, outcome

    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert data["gates"]["lint"]["attempts"] == 2
    assert data["gates"]["lint"]["status"] == "pass"
