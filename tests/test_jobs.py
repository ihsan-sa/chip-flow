"""engine/scripts/jobs.py: a job: true gate runs detached (docs/design.md
1.7). The M1 done criterion: "jobs.py start on a stub gate that sleeps
records a pid, status reports done, and state.gates holds the result."
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import jobs as jobs_mod  # noqa: E402
import state as state_mod  # noqa: E402
from checklib import CheckError  # noqa: E402

SLEEPY_CHECK = '''
import time

def run(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    time.sleep({sleep_s})
    payload = {{"script": "check_sleepy", "status": "pass",
              "counts": {{"total": 0}}, "violations": [],
              "report_schema": 1, "checker_version": 1,
              "generated_at": "2026-01-01T00:00:00+00:00",
              "input": str(args.workspace), "input_digest": None}}
    return payload, args.out
'''

CRASHY_CHECK = "def run(argv=None):\n    raise SystemExit(17)\n"

GATES_YAML_TMPL = '''
version: 1
gates:
  vde:
    harden:
      phase: P6
      tool: {tool}
      job: true
      fail_severities: [error]
      max_count: 0
'''


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    state_mod.State.init(ws, "vde", "counter8")
    return ws


def make_checks_dir(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / "checks"
    d.mkdir(exist_ok=True)
    (d / f"{name}.py").write_text(body, encoding="utf-8")
    return d


def make_gates_yaml(tmp_path: Path, tool: str) -> Path:
    p = tmp_path / "gates.yaml"
    p.write_text(GATES_YAML_TMPL.format(tool=tool), encoding="utf-8")
    return p


def poll_until_not_running(ws: Path, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = jobs_mod.status(ws, job_id, False)[job_id]
        if last["status"] != "running":
            return last
        time.sleep(0.1)
    raise TimeoutError(f"job {job_id} still {last}")


def test_start_records_pid_status_becomes_done_state_holds_result(tmp_path):
    ws = make_ws(tmp_path)
    checks_dir = make_checks_dir(tmp_path, "check_sleepy",
                                 SLEEPY_CHECK.format(sleep_s=0.4))
    gates_yaml = make_gates_yaml(tmp_path, "sleepy")

    rec = jobs_mod.start("harden", ws, "vde", str(gates_yaml), str(checks_dir))
    assert rec["status"] == "running"
    assert isinstance(rec["pid"], int) and rec["pid"] > 0
    jid = rec["job"]
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert data["jobs"][jid]["pid"] == rec["pid"]
    assert data["jobs"][jid]["status"] == "running"

    final = poll_until_not_running(ws, jid)
    assert final["status"] == "done"

    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert data["jobs"][jid]["status"] == "done"
    assert data["jobs"][jid]["finished"] is not None
    assert data["gates"]["harden"]["status"] == "pass"


def test_status_reports_dead_on_unexpected_exit_code(tmp_path):
    ws = make_ws(tmp_path)
    checks_dir = make_checks_dir(tmp_path, "check_crashy", CRASHY_CHECK)
    gates_yaml = make_gates_yaml(tmp_path, "crashy")

    rec = jobs_mod.start("harden", ws, "vde", str(gates_yaml), str(checks_dir))
    jid = rec["job"]
    final = poll_until_not_running(ws, jid)
    assert final["status"] == "dead"


def test_status_unknown_job_refuses(tmp_path):
    ws = make_ws(tmp_path)
    with pytest.raises(CheckError, match="no job"):
        jobs_mod.status(ws, "999", False)


def test_status_all_reconciles_every_job(tmp_path):
    ws = make_ws(tmp_path)
    checks_dir = make_checks_dir(tmp_path, "check_sleepy",
                                 SLEEPY_CHECK.format(sleep_s=0.1))
    gates_yaml = make_gates_yaml(tmp_path, "sleepy")
    rec1 = jobs_mod.start("harden", ws, "vde", str(gates_yaml), str(checks_dir))
    time.sleep(1.0)   # let it finish
    out = jobs_mod.status(ws, None, True)
    assert rec1["job"] in out
    assert out[rec1["job"]]["status"] == "done"


def test_cli_main_start_and_status(tmp_path, capsys):
    ws = make_ws(tmp_path)
    checks_dir = make_checks_dir(tmp_path, "check_sleepy",
                                 SLEEPY_CHECK.format(sleep_s=0.1))
    gates_yaml = make_gates_yaml(tmp_path, "sleepy")
    code = jobs_mod.main(["start", "--gate", "harden", "--workspace", str(ws),
                         "--skill", "vde", "--gates", str(gates_yaml),
                         "--checks-dir", str(checks_dir)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    jid = out["job"]
    time.sleep(1.0)
    code2 = jobs_mod.main(["status", "--workspace", str(ws), "--job", jid])
    assert code2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["jobs"][jid]["status"] == "done"
