"""engine/scripts/jobs.py: a job: true gate runs detached (docs/design.md
1.7). The M1 done criterion: "jobs.py start on a stub gate that sleeps
records a pid, status reports done, and state.gates holds the result."
"""
from __future__ import annotations

import json
import os
import subprocess
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


def test_start_launches_through_eda_python_not_sys_executable(tmp_path, monkeypatch):
    ws = make_ws(tmp_path)
    checks_dir = make_checks_dir(tmp_path, "check_sleepy",
                                 SLEEPY_CHECK.format(sleep_s=0.05))
    gates_yaml = make_gates_yaml(tmp_path, "sleepy")

    captured = {}
    real_run = jobs_mod.subprocess.run

    def spy_run(args, **kwargs):
        if args[:2] == ["bash", "-c"]:
            captured["outer"] = args[2]
        return real_run(args, **kwargs)

    monkeypatch.setattr(jobs_mod.subprocess, "run", spy_run)
    rec = jobs_mod.start("harden", ws, "vde", str(gates_yaml), str(checks_dir))
    poll_until_not_running(ws, rec["job"])

    assert "outer" in captured
    assert str(jobs_mod.EDA_BIN) in captured["outer"]
    assert "python" in captured["outer"]
    # the bug this guards against: launching sys.executable (this test
    # process's OWN interpreter, never the image's) directly - it must
    # never appear ahead of the image path in the launched command.
    assert sys.executable not in captured["outer"]


def test_job_status_trusts_exit_sidecar_over_a_live_pid(tmp_path):
    log = tmp_path / "job-1.log"
    exit_path = tmp_path / "job-1.exit"
    exit_path.write_text("0\n", encoding="utf-8")
    # os.getpid() (this test process) is always "alive" - if the pid check
    # ran before the sidecar, this would report "running" forever even
    # though the sidecar already says the real job finished.
    status_, extra = jobs_mod._job_status({"pid": os.getpid(), "log": str(log)})
    assert status_ == "done"
    assert extra == {"exit_code": 0}


def test_job_status_dead_sidecar_wins_over_a_live_pid(tmp_path):
    log = tmp_path / "job-2.log"
    exit_path = tmp_path / "job-2.exit"
    exit_path.write_text("137\n", encoding="utf-8")  # e.g. SIGKILL'd
    status_, extra = jobs_mod._job_status({"pid": os.getpid(), "log": str(log)})
    assert status_ == "dead"
    assert extra == {"exit_code": 137}


def test_kill_if_dead_sends_sigkill_to_a_lingering_pid(tmp_path):
    ws = make_ws(tmp_path)
    proc = subprocess.Popen(["sleep", "30"])
    try:
        log_dir = ws / "log" / "jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "job-1.log"
        exit_path = log_dir / "job-1.exit"
        # a dead-looking sidecar (a non 0/1 exit code) while the pid is
        # STILL genuinely alive - the case --kill-if-dead exists for.
        exit_path.write_text("137\n", encoding="utf-8")

        st = state_mod.State.load(ws / "state.json")
        jid, _rec = st.start_job("harden", proc.pid, str(log_path))
        st.save()

        out = jobs_mod.status(ws, jid, False, kill_if_dead=True)
        assert out[jid]["status"] == "dead"

        proc.wait(timeout=5)  # would raise TimeoutExpired if not killed
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_kill_if_dead_is_a_noop_when_nothing_is_running(tmp_path):
    ws = make_ws(tmp_path)
    log_dir = ws / "log" / "jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "job-1.log"
    exit_path = log_dir / "job-1.exit"
    exit_path.write_text("137\n", encoding="utf-8")

    st = state_mod.State.load(ws / "state.json")
    # a pid guaranteed to already be gone: let it run and exit first.
    proc = subprocess.Popen(["true"])
    proc.wait()
    dead_pid = proc.pid
    jid, _rec = st.start_job("harden", dead_pid, str(log_path))
    st.save()

    out = jobs_mod.status(ws, jid, False, kill_if_dead=True)  # must not raise
    assert out[jid]["status"] == "dead"


def test_concurrent_starts_get_distinct_ids_and_logs(tmp_path):
    ws = make_ws(tmp_path)
    checks_dir = make_checks_dir(tmp_path, "check_sleepy",
                                 SLEEPY_CHECK.format(sleep_s=0.2))
    gates_yaml = make_gates_yaml(tmp_path, "sleepy")

    results: list[dict] = []
    errors: list[Exception] = []

    def go():
        try:
            results.append(jobs_mod.start("harden", ws, "vde", str(gates_yaml),
                                          str(checks_dir)))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors
    assert len(results) == 4
    ids = [r["job"] for r in results]
    assert len(set(ids)) == 4   # every start reserved its own id
    logs = [r["log"] for r in results]
    assert len(set(logs)) == 4  # its own log too - never shared or unlinked
                                # out from under a sibling start

    for jid in ids:
        poll_until_not_running(ws, jid)
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    # Every job reached a terminal state - never left "running" forever -
    # and none of the four's own pid/log recorded in state.json is another
    # job's (the collision this test exists to rule out). Whether an
    # individual job's own detached gate.py process happened to lose an
    # UNRELATED state.json write race to a sibling finishing at the same
    # moment (gate.py's own record_gate_result has no writer_lock across
    # its load->mutate->save, only State.save()'s compare-and-swap, which
    # raises rather than retries - a separate, pre-existing gap this fix
    # does not touch) can still legitimately land a job on "dead" rather
    # than "done"; that is not the id/log reservation race this test covers.
    for r, jid in zip(results, ids):
        assert data["jobs"][jid]["pid"] == r["pid"]
        assert data["jobs"][jid]["log"] == r["log"]
        assert data["jobs"][jid]["status"] in ("done", "dead")
