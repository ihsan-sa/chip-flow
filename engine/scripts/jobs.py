#!/usr/bin/env python
"""jobs.py - run a job: true gate detached (docs/design.md 1.7).

New at M1 - no /hwde source to port; a board gate always ran in the
foreground, chip-flow's `harden` (LibreLane) can run for the better part of
an hour, so it becomes a job.

    jobs.py start --gate harden --workspace DIR [--skill vde]
                  [--gates FILE] [--checks-dir DIR]
    jobs.py status --job ID --workspace DIR [--kill-if-dead]
    jobs.py status --all --workspace DIR

`start` runs `gate.py --gate <g> --workspace <ws>` detached (its own
session, so it outlives this process), records {pid, log} in state.jobs and
returns immediately - gate.py records the gate's result itself when it
finishes, exactly as a foreground gate does. `status` reports running, done
or dead from the pid: the job's own shell wrapper writes an exit-code
sidecar next to the log when the subprocess finishes, so a live pid means
running, a finished exit-code file means done (exit 0 or 1) or dead
(anything else, or the process is simply gone with no exit file - killed
via SIGKILL). `state.py resume` lists jobs still `running`; a session that
finds one `dead` restarts it.

Exit: 0 ok, 2 error.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import checklib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "jobs"
GATE_PY = SCRIPTS / "gate.py"


def start(gate: str, workspace: Path, skill: str | None,
         gates_path: str | None, checks_dir: str | None) -> dict:
    import state as state_mod
    ws = Path(workspace)
    state_path = ws / "state.json"
    if not state_path.is_file():
        raise CheckError(f"no state.json at {state_path}")
    log_dir = ws / "log" / "jobs"
    log_dir.mkdir(parents=True, exist_ok=True)

    st = state_mod.State.load(state_path)
    jid_preview = str(st.data.get("next_job_id", 1))
    log_path = log_dir / f"job-{jid_preview}.log"
    exit_path = log_dir / f"job-{jid_preview}.exit"
    for stale in (log_path, exit_path):
        if stale.exists():
            stale.unlink()

    cmd = [sys.executable, str(GATE_PY), "--gate", gate,
           "--workspace", str(ws)]
    if skill:
        cmd += ["--skill", skill]
    if gates_path:
        cmd += ["--gates", gates_path]
    if checks_dir:
        cmd += ["--checks-dir", checks_dir]
    inner = f"{shlex.join(cmd)}; echo $? > {shlex.quote(str(exit_path))}"
    # Double-detach: `setsid <inner>` runs in its own session (immune to
    # this process's death), backgrounded and disowned from the OUTER
    # bash, which then only echoes the detached pid and exits at once.
    # jobs.py never becomes that pid's parent - init is - so it is never a
    # zombie in a long-lived caller (an orchestrator session, or this
    # module's own test suite calling start() directly rather than via a
    # fresh `eda python jobs.py start` each time).
    outer = (f"setsid bash -c {shlex.quote(inner)} </dev/null "
            f">{shlex.quote(str(log_path))} 2>&1 & echo $!")
    launch = subprocess.run(["bash", "-c", outer], cwd=str(ws),
                            capture_output=True, text=True)
    if launch.returncode != 0 or not launch.stdout.strip():
        raise CheckError(f"could not launch job: {launch.stderr.strip()}")
    pid = int(launch.stdout.strip())

    # reload under the writer lock: the detached job is already running
    # independently by now, so nothing races the state write below on this
    # process's own account, but another CLI writer might have landed since the
    # preview load - State.save()'s compare-and-swap catches that.
    st = state_mod.State.load(state_path)
    jid, rec = st.start_job(gate, pid, str(log_path))
    st.save()
    return {"job": jid, **rec}


def _job_status(job: dict) -> tuple[str, dict | None]:
    """(status, updates|None) derived from the pid + exit-code sidecar,
    independent of what state.json currently says (the caller reconciles)."""
    pid = job["pid"]
    alive = True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        alive = False
    except PermissionError:
        alive = True  # exists, owned by someone else - treat as running
    if alive:
        return "running", None
    exit_path = Path(job["log"]).with_suffix(".exit")
    if exit_path.is_file():
        try:
            code = int(exit_path.read_text(encoding="utf-8").strip())
        except ValueError:
            return "dead", None
        return ("done" if code in (0, 1) else "dead"), {"exit_code": code}
    return "dead", None


def status(workspace: Path, job_id: str | None, all_jobs: bool) -> dict:
    import state as state_mod
    ws = Path(workspace)
    state_path = ws / "state.json"
    st = state_mod.State.load(state_path)
    ids = list(st.data["jobs"]) if all_jobs else [job_id]
    out = {}
    changed = False
    for jid in ids:
        job = st.data["jobs"].get(jid)
        if job is None:
            raise CheckError(f"no job {jid!r}")
        if job["status"] == "running":
            new_status, extra = _job_status(job)
            if new_status != "running":
                st.update_job(jid, status=new_status, result=extra)
                changed = True
        out[jid] = st.data["jobs"][jid]
    if changed:
        st.save()
    return out


def main(argv: list[str] | None = None) -> int:
    checklib.utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start")
    p.add_argument("--gate", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--skill", help="passed through to gate.py")
    p.add_argument("--gates", help="passed through to gate.py")
    p.add_argument("--checks-dir", help="passed through to gate.py (tests)")
    p.add_argument("--out")

    p = sub.add_parser("status")
    p.add_argument("--workspace", required=True)
    p.add_argument("--job")
    p.add_argument("--all", action="store_true")
    p.add_argument("--out")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "start":
            payload = {"script": SCRIPT, "cmd": "start",
                      **start(args.gate, Path(args.workspace), args.skill,
                              args.gates, args.checks_dir)}
        else:
            if not args.all and not args.job:
                ap.error("status needs --job ID or --all")
            payload = {"script": SCRIPT, "cmd": "status",
                      "jobs": status(Path(args.workspace), args.job, args.all)}
    except Exception as exc:  # noqa: BLE001  (any error -> exit 2)
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}",
                          "remediation": str(exc)}))
        return 2

    text = json.dumps(payload, indent=1)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
