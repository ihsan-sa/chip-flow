"""checklib.py - shared plumbing every check script (check_<gate>.py) uses.

Ported from /hwde's scripts/lib/checklib.py (chip-flow docs/design.md 1.3),
domain swapped: a PCB check's finding sits at a board (x, y); a chip-flow
finding sits at a (file, module, line) - a Verilog line, a SPICE netlist
line, a spec.yaml requirement. `cluster_violations.py` groups on that triple
instead of net + spatial radius (design.md 1.3: "the clustering key becomes
file, module and finding kind").

Every check script emits the normalized finding schema:
    {check, severity, file, module, line, kind, refs, msg, source, items}
plus check-specific extra keys downstream consumers (cluster_violations.py,
fixer agents) may read but must not require.

Report payload shape (what gate.py's evaluate() expects from every
check_<gate>.py):
    {script, status: pass|violations|error, counts{total, by_severity,
     by_source}, violations: [...], ...check-specific facts}

Exit contract (docs/design.md 1.1): argparse, JSON to stdout or --out,
exit 0 pass, 1 findings, 2 error with a remediation string, no prompts,
ASCII.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


class CheckError(RuntimeError):
    """The check cannot run (bad inputs, unusable workspace). CLI exit 2."""


# Version of the report payload shape gate.py's --report validation relies
# on. Bump when a field gate.py depends on changes meaning.
REPORT_SCHEMA = 1

# Version of the CHECKER SEMANTICS behind the reports. Bump on any change to
# what a check finds or how findings are keyed - durable waivers (M3) bind
# this value, so a bump invalidates every recorded waiver until a human
# re-approves it under the new checkers. Coarse on purpose: one bump point,
# conservative direction.
CHECKER_VERSION = 1


def _statelib():
    """Import engine/lib/statelib regardless of whether checklib was
    imported as `checklib` (engine/lib on sys.path) or `lib.checklib`."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import statelib
    return statelib


def stamp(payload: dict, input_path) -> dict:
    """Attach the provenance fields gate.py's --report validates: schema
    version, generation time (UTC), input path and its NORMALIZED design
    digest (statelib norms - byte hashes churn on EOL/timestamp noise).
    Digest failures degrade to None (the report stays usable standalone;
    gate.py refuses a report without a digest)."""
    from datetime import datetime, timezone
    payload["report_schema"] = REPORT_SCHEMA
    payload["checker_version"] = CHECKER_VERSION
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    if input_path is not None:
        p = Path(input_path)
        payload["input"] = str(p)
        try:
            sl = _statelib()
            payload["input_digest"] = sl.hash_artifact(p, sl.norm_for_path(p))
        except Exception:
            payload["input_digest"] = None
    return payload


def utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def rnd(v: float, nd: int = 4) -> float:
    return round(float(v), nd)


def violation(check: str, severity: str, file: str | None,
              module: str | None, kind: str | None, refs, msg: str,
              source: str, line: int | None = None, **extras) -> dict:
    """Build one normalized finding. The clustering key is (file, module,
    kind) - never a spatial position (design.md 1.3)."""
    v = {
        "check": check,
        "severity": severity,
        "file": file,
        "module": module,
        "line": line,
        "kind": kind,
        "refs": sorted(set(refs or [])),
        "msg": msg,
        "source": source,
        "items": [{"msg": msg, "file": file, "line": line}],
    }
    v.update(extras)
    return v


def summarize(violations: list[dict]) -> dict:
    counts = {"total": len(violations)}
    by_sev: dict[str, int] = {}
    by_src: dict[str, int] = {}
    for v in violations:
        by_sev[v["severity"]] = by_sev.get(v["severity"], 0) + 1
        by_src[v["source"]] = by_src.get(v["source"], 0) + 1
    counts["by_severity"] = by_sev
    counts["by_source"] = by_src
    return counts


def report(script: str, workspace, violations: list[dict], **facts) -> dict:
    payload = {
        "script": script,
        "status": "violations" if violations else "pass",
        "counts": summarize(violations),
        "violations": violations,
        **facts,
    }
    return stamp(payload, workspace)


def load_json(path, what: str) -> dict:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CheckError(f"cannot read {what} {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CheckError(f"{what} {p} is not valid JSON: {exc}") from exc


def emit(payload: dict, out: str | None) -> int:
    """Write/print the report; return the exit code for its status."""
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return {"pass": 0, "violations": 1}.get(payload.get("status"), 2)


def cli_wrap(script: str, fn) -> int:
    """Run fn() -> (payload, out_path); map any exception to the exit-2
    error JSON contract, with a `remediation` string alongside `error`."""
    utf8_stdout()
    try:
        payload, out = fn()
    except Exception as exc:  # noqa: BLE001  (contract: any error -> exit 2)
        print(json.dumps({"script": script, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}",
                          "remediation": str(exc)}))
        return 2
    return emit(payload, out)


# A tool that forks its own workers (mcy -> mutate_runner.py -> vvp, sby ->
# solvers) outlives a plain subprocess.run(timeout=): the timeout kills the
# one child it started, and a caller that dies kills nothing. Nine mutate
# sims once ran for two days that way, reparented to init, after their
# pytest runs were gone. bin/eda exec()s every tool through the image's
# ld.so, so the "wrapper" is the tool's own process and passes signals on
# fine; what was missing is killing the whole tree. run_group() puts the
# tool in a process group of its own and SIGKILLs that group when the call
# ends for any reason: exit, timeout, an exception (KeyboardInterrupt too),
# and - through a /bin/sh watchdog holding the read end of a pipe only this
# process writes - this process dying outright, SIGKILL included.
_GUARD = 'read _ ; kill -KILL -"$0" 2>/dev/null'  # dash: no --


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_group(cmd: list[str], timeout: float | None = None,
              **popen_kw) -> subprocess.CompletedProcess:
    """subprocess.run(cmd, timeout=timeout, **popen_kw) for a tool whose
    children must not outlive the call: every process the tool starts dies
    with it, on timeout (TimeoutExpired is raised as subprocess.run does),
    on exit, and when this process is killed. popen_kw takes what Popen
    does (cwd, capture_output, text, encoding, errors, env...)."""
    if popen_kw.pop("capture_output", False):
        popen_kw["stdout"] = popen_kw["stderr"] = subprocess.PIPE
    proc = subprocess.Popen(cmd, start_new_session=True, **popen_kw)
    # its own session too, so a terminal ^C cannot take the guard down
    # before this process has cleaned up; a clean env, because the caller's
    # may carry bin/eda's LD_PRELOAD, built for the image's libc, not /bin/sh's
    guard = subprocess.Popen(["/bin/sh", "-c", _GUARD, str(proc.pid)],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True,
                             env={"PATH": "/usr/bin:/bin"})
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        # communicate() alone would wait for EOF on the pipes, which a
        # backgrounded straggler holds open after the tool itself has exited
        while True:
            left = None if deadline is None else deadline - time.monotonic()
            try:
                out, err = proc.communicate(
                    timeout=0.5 if left is None else max(0.0, min(0.5, left)))
                break
            except subprocess.TimeoutExpired:
                if proc.poll() is not None:
                    _kill_group(proc.pid)
                    out, err = proc.communicate()
                    break
                if left is not None and left <= 0:
                    _kill_group(proc.pid)
                    out, err = proc.communicate()
                    raise subprocess.TimeoutExpired(cmd, timeout, out,
                                                    err) from None
    finally:
        # the guard goes first, so it cannot fire later at a pgid reused
        guard.kill()
        guard.wait()
        guard.stdin.close()
        _kill_group(proc.pid)  # stragglers the leader left behind
        if proc.returncode is None:
            proc.wait()
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
