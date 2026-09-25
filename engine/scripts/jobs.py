#!/usr/bin/env python
"""jobs.py - run a job: true gate detached (docs/design.md 1.7).

New at M1 - no /hwde source to port; a board gate always ran in the
foreground, chip-flow's `harden` (LibreLane) can run for the better part of
an hour, so it becomes a job.

    jobs.py start --gate harden --workspace DIR [--skill vde]
                  [--gates FILE] [--checks-dir DIR]
    jobs.py status --job ID --workspace DIR [--kill-if-dead]
    jobs.py status --all --workspace DIR [--kill-if-dead]

`start` runs `gate.py --gate <g> --workspace <ws> --out
<ws>/reports/gate-<g>.json` detached (its own
session, so it outlives this process) THROUGH `<repo>/bin/eda python`
(docs/design.md 1.2) - never `sys.executable` directly, which is the
image's own python3.12 ELF and, execve'd without eda's loader wrapping,
hits the same host-glibc-vs-image-glibc mismatch every other image binary
does (bin/eda's own header). Records {pid, log} in state.jobs and returns
immediately - gate.py records the gate's result itself when it finishes,
exactly as a foreground gate does, and rewrites the same gate report the
router's foreground step does (a templated `{...}` gate names no report), so
no earlier run's FAIL report sits beside the job's passing state. The job's
log keeps gate.py's one-line summary. `status` reports running, done or dead:
the job's own shell wrapper writes an exit-code sidecar next to the log
when the subprocess finishes, and that sidecar is read FIRST and trusted
over the pid whenever it exists - a live pid alone is not proof the job is
still running (the number can be recycled onto an unrelated process once
the real one has exited, and a foreign pid's `kill(pid, 0)` can raise
PermissionError for the same reason). Only while no sidecar exists yet does
a live pid mean "running". `--kill-if-dead` additionally SIGKILLs a job's
pid once its status resolves to `dead`, best-effort: harmless if the
process is already gone, and closes the gap where a dead-looking job (a
corrupt or missing exit code) still has a lingering process a naive
restart would then race. `state.py resume` lists jobs still `running`; a
session that finds one `dead` restarts it.

Exit: 0 ok, 2 error.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import checklib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "jobs"
GATE_PY = SCRIPTS / "gate.py"
EDA_BIN = REPO / "bin" / "eda"


def start(gate: str, workspace: Path, skill: str | None,
         gates_path: str | None, checks_dir: str | None) -> dict:
    import safelib
    import state as state_mod
    # Absolute before anything is derived from it: the job runs with
    # cwd=ws, so a relative workspace (the router's own `blocks/<name>`)
    # would point the log redirect and gate.py's --workspace at
    # ws/ws/..., and the job would die before writing a line.
    ws = Path(workspace).resolve()
    gates_path = str(Path(gates_path).resolve()) if gates_path else None
    checks_dir = str(Path(checks_dir).resolve()) if checks_dir else None
    state_path = ws / "state.json"
    if not state_path.is_file():
        raise CheckError(f"no state.json at {state_path}")
    log_dir = ws / "log" / "jobs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # ONE writer-lock hold across reserving next_job_id, picking the
    # log/exit paths that id names, launching, and recording the job:
    # previously the id was only PREVIEWED here (an unlocked read), then
    # re-read for real, under the lock, only at the very end - so two
    # concurrent starts could preview the SAME id, compute the SAME
    # job-N.log/.exit names, each unlink whatever the other had just
    # created, and race writing to the files the OS had already handed to
    # two different launched processes. Reserving the id up front, under
    # the same lock state.py's own CLI holds across load->mutate->save,
    # means the preview IS the reservation: no other start() can observe
    # this next_job_id until this one has consumed and incremented it.
    with safelib.writer_lock(state_path, what="state.json"):
        st = state_mod.State.load(state_path)
        jid_preview = str(st.data.get("next_job_id", 1))
        log_path = log_dir / f"job-{jid_preview}.log"
        exit_path = log_dir / f"job-{jid_preview}.exit"
        for stale in (log_path, exit_path):
            if stale.exists():
                stale.unlink()

        # Through `eda python`, never sys.executable directly (docs/
        # design.md 1.2): sys.executable is the image's own python3.12
        # ELF, and execve'd by bash without eda's loader wrapping it hits
        # the same host-glibc-vs-image-glibc mismatch every other image
        # binary does.
        cmd = [str(EDA_BIN), "python", str(GATE_PY), "--gate", gate,
               "--workspace", str(ws)]
        if skill:
            cmd += ["--skill", skill]
        if "{" not in gate:
            cmd += ["--out", str(ws / "reports" / f"gate-{gate}.json")]
        if gates_path:
            cmd += ["--gates", gates_path]
        if checks_dir:
            cmd += ["--checks-dir", checks_dir]
        inner = f"{shlex.join(cmd)}; echo $? > {shlex.quote(str(exit_path))}"
        # Double-detach: `setsid <inner>` runs in its own session (immune to
        # this process's death), backgrounded and disowned from the OUTER
        # bash, which then only echoes the detached pid and exits at once.
        # jobs.py never becomes that pid's parent - init is - so it is
        # never a zombie in a long-lived caller (an orchestrator session,
        # or this module's own test suite calling start() directly rather
        # than via a fresh `eda python jobs.py start` each time).
        outer = (f"setsid bash -c {shlex.quote(inner)} </dev/null "
                f">{shlex.quote(str(log_path))} 2>&1 & echo $!")
        launch = subprocess.run(["bash", "-c", outer], cwd=str(ws),
                                capture_output=True, text=True)
        if launch.returncode != 0 or not launch.stdout.strip():
            raise CheckError(f"could not launch job: {launch.stderr.strip()}")
        pid = int(launch.stdout.strip())

        jid, rec = st.start_job(gate, pid, str(log_path))
        st.save()
    return {"job": jid, **rec}


def _job_status(job: dict) -> tuple[str, dict | None]:
    """(status, updates|None), independent of what state.json currently
    says (the caller reconciles). The exit-code sidecar is read FIRST and
    trusted over the pid whenever it exists: the job's own shell wrapper
    writes it exactly once, after the real subprocess has already exited,
    so its presence is authoritative regardless of what a since-recycled
    pid number now names. A pid is only consulted as a fallback while no
    sidecar exists yet - `kill(pid, 0)` succeeding proves nothing once the
    original process could have exited and the OS handed that same number
    to something else entirely (and PermissionError, "exists, owned by
    someone else", is exactly the shape that recycling takes across a
    privilege boundary)."""
    exit_path = Path(job["log"]).with_suffix(".exit")
    if exit_path.is_file():
        try:
            code = int(exit_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return "dead", None
        return ("done" if code in (0, 1) else "dead"), {"exit_code": code}
    pid = job["pid"]
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead", None
    except PermissionError:
        return "running", None  # exists, owned by someone else - treat as running
    return "running", None


def status(workspace: Path, job_id: str | None, all_jobs: bool,
          kill_if_dead: bool = False) -> dict:
    import safelib
    import state as state_mod
    ws = Path(workspace)
    state_path = ws / "state.json"
    # Hold the writer lock across load -> update_job -> save, same as
    # start() above and gate.py's record_gate_result: State.save() is only
    # a compare-and-swap at the write, so an unlocked load here could race
    # a detached gate.py (or a concurrent status --all-if-dead) landing its
    # own write in between, and this call's save() would raise
    # StaleWriteError on a perfectly good status poll.
    with safelib.writer_lock(state_path, what="state.json"):
        st = state_mod.State.load(state_path)
        ids = list(st.data["jobs"]) if all_jobs else [job_id]
        out = {}
        changed = False
        for jid in ids:
            job = st.data["jobs"].get(jid)
            if job is None:
                raise CheckError(f"no job {jid!r}")
            cur_status = job["status"]
            if cur_status == "running":
                new_status, extra = _job_status(job)
                if new_status != "running":
                    st.update_job(jid, status=new_status, result=extra)
                    changed = True
                cur_status = new_status
            if kill_if_dead and cur_status == "dead":
                # Best-effort cleanup, not a status source: a dead-looking
                # job (a missing/corrupt exit sidecar, say) can still have a
                # lingering process, and a caller about to restart the gate
                # on this workspace should not race it. ProcessLookupError
                # (already gone) and PermissionError (not this process's to
                # kill) are both fine outcomes here, never a reason to
                # refuse the status report itself.
                try:
                    os.kill(st.data["jobs"][jid]["pid"], signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
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
    p.add_argument("--kill-if-dead", action="store_true", dest="kill_if_dead",
                   help="SIGKILL a job's pid once its status resolves to "
                        "dead (best-effort; harmless if it is already gone)")
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
                      "jobs": status(Path(args.workspace), args.job, args.all,
                                    args.kill_if_dead)}
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
