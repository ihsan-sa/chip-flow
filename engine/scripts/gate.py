#!/usr/bin/env python
"""gate.py - evaluate a pipeline gate (docs/design.md 1.5).

    gate.py --gate <name> --workspace DIR [--skill vde|ade|msde]
            [--out FILE] [--commit MSG] [--no-record]
    gate.py --gate <name> --report <check-report.json> [--workspace DIR]
    gate.py --list [--skill vde]

A gate (defined in reference/gates.yaml, one table per skill) runs its
check_<tool>.py (M1: every tool is the stub, engine/scripts/check_stub.py -
docs/design.md, "### M1.") against the workspace and applies pass criteria:
it FAILS when the count of findings whose severity is in the gate's
fail_severities exceeds max_count. On pass, --commit MSG git-adds and
commits the block's own workspace directory, never the whole repo; it never
commits on failure and never pushes.

JSON to stdout (or --out): {script, gate, skill, status:pass|fail, counts,
criteria, failing:[findings that triggered], record_result, commit_result?}.
An error goes to --out as well, as {script, gate, status:error, error,
remediation}, so a caller reading --out never finds an earlier run there.
Exit 0 = pass, 1 = fail, 2 = error (bad gate name, missing tool, unreadable
workspace, or a requested record/commit that did not happen).

Ported from /hwde's scripts/gate.py (docs/design.md 1.3), generalized: hwde
dispatched per KiCad tool (erc/drc/verify/place/dfm/sim, each its own
Python function calling kc.py or a sibling script with bespoke args); every
chip-flow gate instead runs the SAME shape - `check_<tool>.py --workspace
<ws>` - dynamically imported and called as a sibling script's `run(argv)`,
so a new gate needs no change here, only a new check_<gate>.py and a
gates.yaml row. Waiver/durability handling (release-context strict gates)
is /hwde's U5 and is M3's job here (docs/design.md, "### M3.") - not ported
yet; `strict` and `--waivers` are reserved but unused at M1.

The gate RECORDS ITSELF. When the input sits inside a workspace (a
directory holding a state.json - found by walking the input's parents, or
named outright with --workspace), the result goes through
state.record_gate: input hashes, attempt count, stale-mark clearing all
apply. Pass AND fail are recorded (the fix loop wants every attempt). A
scratch/corpus input with no workspace records nothing, and the result says
so (`record_result.recorded: false` + reason); --no-record opts out
explicitly. A REQUESTED-but-failed record is an operational error (exit 2),
exactly like a requested-but-failed commit: a caller keying on exit 0 must
not believe the evidence was preserved when it was not.
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import traceback
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import checklib  # noqa: E402
import safelib  # noqa: E402
import statelib  # noqa: E402

import yaml  # noqa: E402

DEFAULT_GATES = ENGINE / "reference" / "gates.yaml"
MAX_REPORT_AGE_H = 24.0


def load_gates(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    gates = data.get("gates", {})
    if not gates:
        raise RuntimeError(f"no gates defined in {path}")
    return gates


def skill_of(workspace: Path | None, explicit: str | None) -> str:
    if explicit:
        return explicit
    if workspace is not None and (Path(workspace) / "state.json").is_file():
        data = checklib.load_json(Path(workspace) / "state.json", "state.json")
        skill = data.get("skill")
        if skill:
            return skill
    raise RuntimeError(
        "cannot tell which skill this gate belongs to: give --workspace "
        "(a directory with a state.json carrying 'skill') or --skill "
        "explicitly")


def _reject_unless_completed(payload: dict, bad) -> None:
    """A check result counts only when it is 'pass' or 'violations' with a
    real violations list - 'skipped', None, a missing key, or anything else
    is a refusal, never silently recorded as a pass. `bad` is the caller's
    own raiser, so each call site keeps its own message prefix. Shared by
    run_report_for_gate (a check that just ran) and validate_report (a
    --report file), so the two can never drift apart on what "completed"
    means."""
    status = payload.get("status")
    if status not in ("pass", "violations"):
        bad(f"status {status!r} is not a completed run")
    if not isinstance(payload.get("violations"), list):
        bad("no 'violations' list - a missing key is invalid, not empty")


def run_report_for_gate(gate: dict, workspace: Path,
                        checks_dir: Path | None = None) -> dict:
    """Import check_<tool>.py (sibling script, or --checks-dir for tests)
    and call its run(["--workspace", ...]) -> (payload, out). Every real
    check_<gate>.py follows this one shape; only the stub differs in that
    it always raises."""
    tool = gate.get("tool")
    if not tool:
        raise RuntimeError("gate has no 'tool'")
    search_dir = Path(checks_dir) if checks_dir else SCRIPTS
    mod_name = f"check_{tool}"
    script_path = search_dir / f"{mod_name}.py"
    if not script_path.is_file():
        raise RuntimeError(
            f"check script not found: {script_path} (gate tool {tool!r} - "
            "not built yet, or gates.yaml points at the wrong stem)")
    # Force a fresh import from search_dir every time, never a cached
    # sys.modules entry: tests reuse the same module name (check_stub,
    # check_fakegate) from different --checks-dir directories in one
    # process, and importlib.reload() alone would keep replaying whichever
    # directory's file was imported FIRST (reload uses the module's
    # original __spec__, not the current sys.path order).
    sys.modules.pop(mod_name, None)
    if str(search_dir) in sys.path:
        sys.path.remove(str(search_dir))
    sys.path.insert(0, str(search_dir))
    mod = importlib.import_module(mod_name)
    payload, _out = mod.run(["--workspace", str(workspace)])
    if payload.get("status") == "error":
        raise RuntimeError(f"{mod_name} report status is error: "
                           f"{payload.get('error')}")

    def bad(msg: str):
        raise RuntimeError(f"{mod_name} report refused: {msg}")

    _reject_unless_completed(payload, bad)
    return payload


def validate_report(gate_name: str, gate: dict, report: dict,
                    max_age_h: float = MAX_REPORT_AGE_H) -> None:
    """Refuse any --report that is not a fresh, successful, matching report
    for THIS gate. Every refusal raises -> exit 2, never a pass."""
    def bad(msg: str):
        raise RuntimeError(f"--report refused: {msg}")

    if not isinstance(report, dict):
        bad("not a JSON object")
    if report.get("report_schema") != checklib.REPORT_SCHEMA:
        bad(f"report_schema {report.get('report_schema')!r} (expected "
            f"{checklib.REPORT_SCHEMA}) - regenerate with the current tools")
    expected = f"check_{gate.get('tool')}"
    if report.get("script") != expected:
        bad(f"produced by {report.get('script')!r} but the {gate_name!r} "
            f"gate's tool expects {expected!r}")
    _reject_unless_completed(report, bad)

    from datetime import datetime, timezone
    gen = report.get("generated_at")
    try:
        gen_dt = datetime.fromisoformat(str(gen))
    except (TypeError, ValueError):
        gen_dt = None
    if gen_dt is None:
        bad(f"unparsable generated_at {gen!r}")
    if gen_dt.tzinfo is None:
        gen_dt = gen_dt.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 3600.0
    if age_h < -5 / 60.0:
        bad(f"generated_at {gen} is in the future")
    if age_h > max_age_h:
        bad(f"report is {age_h:.1f} h old, staleness bound {max_age_h} h - "
            "re-run the tool")


# checklib's own report envelope keys (checklib.report/stamp): never a
# check-specific fact, so never duplicated into a gate result's own "facts".
_STANDARD_REPORT_KEYS = {
    "script", "status", "counts", "violations", "report_schema",
    "checker_version", "generated_at", "input", "input_digest",
}


def evaluate(gate_name: str, gate: dict, report: dict) -> dict:
    fail_sev = set(gate.get("fail_severities") or ["error"])
    max_count = int(gate.get("max_count", 0))
    violations = report.get("violations", [])
    failing = [v for v in violations if v.get("severity") in fail_sev]
    passed = len(failing) <= max_count
    result = {
        "script": "gate",
        "gate": gate_name,
        "phase": gate.get("phase"),
        "tool": gate.get("tool"),
        "input_digest": report.get("input_digest"),
        "status": "pass" if passed else "fail",
        "criteria": {"fail_severities": sorted(fail_sev), "max_count": max_count},
        "counts": report.get("counts", {}),
        "failing_count": len(failing),
        "failing": failing,
        # The check's FULL violations list, every severity - not just the
        # fail_severities-matching subset above (M5, found running the fix
        # loop for real on /vde's own mutate gate: most survivor_* findings
        # are severity "info" by design, gates.yaml's fail_severities is
        # [error], so `failing` alone showed a fixer 1 of 12 real survivors
        # - the other 11 existed only in the check script's own raw report,
        # which nothing downstream of gate.py ever saw again). Pass/fail
        # and recording both still key off `failing`/`failing_count` alone,
        # unchanged; this is purely additional diagnostic fidelity for a
        # fixer or fix_dispatch.py to cluster against.
        "violations": violations,
    }
    # Whatever the check script reported beyond the standard envelope - a
    # kill rate and survivors by class (mutate), tests_run (sim/holdout), a
    # top module name (lint) - surfaces here rather than being silently
    # dropped: "gate.py --gate mutate ... reports a kill rate and survivors
    # by class" (docs/design.md "### M2.") needs this to actually be true of
    # the GATE's own output, not just the check script's.
    facts = {k: v for k, v in report.items() if k not in _STANDARD_REPORT_KEYS}
    if facts:
        result["facts"] = facts
    return result


find_workspace = statelib.find_workspace


def record_gate_result(skill: str, gate_name: str, gate: dict, result: dict,
                       workspace: Path | None) -> dict:
    """Record the result in the workspace's state.json.

    Holds the state.json writer lock across load -> record_gate -> save, not
    just around save() itself: State.save() is a compare-and-swap that only
    takes the lock for the write, so two gates recording into the same
    workspace at once (two jobs.py jobs, or two `gate.py` invocations from
    parallel skill runs) could both load the same base bytes, both mutate,
    and have the second's save() raise StaleWriteError even though nothing
    else touched the file in between - a race, not a real conflict. state.py's
    own CLI (`record-gate` and every other mutator) already wraps its
    load/mutate/save in exactly this lock (state.py main(), "one OS-exclusive
    hold across load -> mutate -> save"); this mirrors it so gate.py's own
    self-recording path gets the same safety. safelib.writer_lock is
    re-entrant per-thread/per-process, so State.save()'s own internal
    `with writer_lock(...)` nests for free."""
    ws = find_workspace(None, str(workspace) if workspace else None)
    if ws is None:
        return {"ok": True, "recorded": False,
                "reason": "no state.json above the workspace - nothing to "
                          "record (corpus/scratch input)"}
    imap = statelib.load_map()
    if gate_name not in (imap["gate_inputs"].get(skill) or {}):
        return {"ok": True, "recorded": False,
                "workspace": str(ws).replace("\\", "/"),
                "reason": f"gate {gate_name!r} has no gate_inputs entry for "
                          f"skill {skill!r} in invalidation.yaml - a result "
                          "with no input hashes is not evidence, so it is "
                          "not recorded"}
    state_path = ws / "state.json"
    try:
        import state as state_mod  # sibling script
        with safelib.writer_lock(state_path, what="state.json"):
            st = state_mod.State.load(state_path)
            g = st.record_gate(gate_name, result, gate.get("phase"))
            st.save()
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return {"ok": False, "recorded": False,
                "workspace": str(ws).replace("\\", "/"),
                "reason": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "recorded": True,
            "workspace": str(ws).replace("\\", "/"), "gate": gate_name,
            "status": g["status"], "attempts": g["attempts"],
            "inputs": (g.get("last") or {}).get("inputs")}


def repo_root(start: Path) -> Path | None:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          cwd=str(start), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def git_commit_on_pass(msg: str, workspace: Path) -> dict:
    """Stage the block's own workspace and commit (only called on gate
    pass). Never `git add -A`: a parallel session's dirty files never ride
    along in a gate commit. Never pushes."""
    root = repo_root(workspace)
    if root is None:
        return {"committed": False, "ok": False,
                "reason": f"{workspace} is not inside a git repo"}

    def git(*args):
        return subprocess.run(["git", *args], cwd=str(root),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    try:
        ws_rel = workspace.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return {"committed": False, "ok": False,
                "reason": f"workspace {workspace} is outside the repo {root}"}

    def in_scope(p: str) -> bool:
        return p == ws_rel or p.startswith(ws_rel + "/")

    status = git("status", "--porcelain", "-uall")
    if status.returncode != 0:
        return {"committed": False, "ok": False,
                "reason": f"git status failed: {status.stderr.strip()}"}
    if not status.stdout.strip():
        return {"committed": False, "ok": True, "reason": "nothing to commit"}
    dirty = [ln[3:].strip().strip('"').replace("\\", "/")
             for ln in status.stdout.splitlines() if ln.strip()]

    staged = git("diff", "--cached", "--name-only")
    if staged.returncode != 0:
        return {"committed": False, "ok": False,
                "reason": f"git diff --cached failed: {staged.stderr.strip()}"}
    pre_outside = [p.strip().strip('"').replace("\\", "/")
                   for p in staged.stdout.splitlines() if p.strip()]
    pre_outside = [p for p in pre_outside if not in_scope(p)]
    if pre_outside:
        return {"committed": False, "ok": False, "scope": ws_rel,
                "reason": "pre-staged paths outside the workspace (a commit "
                          "would sweep them): "
                          + ", ".join(sorted(pre_outside)[:20])}

    inside = [d for d in dirty if in_scope(d)]
    outside = [d for d in dirty if d not in inside]
    extras: dict = {"scope": ws_rel}
    if outside:
        extras["excluded_dirty"] = outside
    if not inside:
        return {"committed": False, "ok": True,
                "reason": "nothing to commit inside the workspace", **extras}
    add = git("add", "--", ws_rel)
    if add.returncode != 0:
        return {"committed": False, "ok": False,
                "reason": f"git add failed: {add.stderr.strip()}", **extras}
    commit = git("commit", "-m", msg)
    if commit.returncode != 0:
        return {"committed": False, "ok": False,
                "reason": f"git commit failed: {commit.stderr.strip()}",
                **extras}
    rev = git("rev-parse", "--short", "HEAD")
    return {"committed": True, "ok": True, "commit": rev.stdout.strip(),
            **extras}


def main(argv: list[str] | None = None) -> int:
    checklib.utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gate", help="gate name from gates.yaml")
    ap.add_argument("--gates", default=str(DEFAULT_GATES),
                    help="path to gates.yaml")
    ap.add_argument("--skill", choices=statelib.SKILLS,
                    help="skill the gate belongs to (default: read from "
                         "--workspace's state.json)")
    ap.add_argument("--workspace", help="block workspace (state.json inside)")
    ap.add_argument("--checks-dir", help="directory holding check_<tool>.py "
                    "(default: engine/scripts, alongside gate.py; tests "
                    "point this elsewhere)")
    ap.add_argument("--report", help="evaluate this report JSON instead of "
                                     "running the tool")
    ap.add_argument("--max-report-age-h", type=float, default=MAX_REPORT_AGE_H)
    ap.add_argument("--out", help="write gate result JSON here instead of stdout")
    ap.add_argument("--commit", metavar="MSG",
                    help="git commit the workspace on gate pass with this message")
    ap.add_argument("--no-record", action="store_true", dest="no_record",
                    help="evaluate only - do not record the result in "
                         "state.json")
    ap.add_argument("--list", action="store_true", help="list gates and exit")
    args = ap.parse_args(argv)

    try:
        gates = load_gates(Path(args.gates))
        if args.list:
            listing = {skill: {name: {"phase": g.get("phase"),
                                      "tool": g.get("tool")}
                               for name, g in rows.items()}
                      for skill, rows in gates.items()
                      if not args.skill or skill == args.skill}
            print(json.dumps({"script": "gate", "gates": listing}, indent=2))
            return 0
        if not args.gate:
            ap.error("--gate is required (or use --list)")

        skill = skill_of(Path(args.workspace) if args.workspace else None,
                         args.skill)
        rows = gates.get(skill) or {}
        if args.gate not in rows:
            raise RuntimeError(
                f"unknown gate {args.gate!r} for skill {skill!r}; known: "
                f"{', '.join(rows)}")
        gate = rows[args.gate]

        if args.report:
            report = json.loads(Path(args.report).read_text(encoding="utf-8"))
            validate_report(args.gate, gate, report,
                            max_age_h=args.max_report_age_h)
        else:
            if not args.workspace:
                ap.error("--workspace is required unless --report is given")
            report = run_report_for_gate(
                gate, Path(args.workspace).resolve(),
                Path(args.checks_dir) if args.checks_dir else None)

        result = evaluate(args.gate, gate, report)

        if not args.no_record:
            result["record_result"] = record_gate_result(
                skill, args.gate, gate, result,
                Path(args.workspace) if args.workspace else None)

        if result["status"] == "pass" and args.commit:
            if not args.workspace:
                raise RuntimeError("--commit requires --workspace")
            # Write the report first so the commit carries this run's
            # report, not the last one's, and leaves no modified copy.
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(json.dumps(result, indent=2),
                                          encoding="utf-8")
            result["commit_result"] = git_commit_on_pass(
                args.commit, Path(args.workspace))
    except Exception as exc:  # noqa: BLE001 - the exit-2 contract
        # The error goes where the result would have: a caller reading
        # --out must not find the last run's pass there instead.
        text = json.dumps({"script": "gate", "gate": args.gate,
                           "status": "error",
                           "error": traceback.format_exc(),
                           "remediation": f"{type(exc).__name__}: {exc}"},
                          indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"gate {args.gate}: ERROR ({exc}) - see {args.out}",
                  file=sys.stderr)
        else:
            print(text)
        return 2

    text = json.dumps(result, indent=2)
    cr = result.get("commit_result")
    if args.out and cr is not None and cr.get("committed"):
        # Already written and committed above; rewriting it to add the
        # commit id would leave the tree dirty again.
        print(f"gate {args.gate}: committed {cr.get('commit')}",
              file=sys.stderr)
    elif args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)

    n = result.get("failing_count", 0)
    print(f"gate {args.gate}: {result['status'].upper()} "
          f"({n} failing / {result['counts'].get('total', 0)} total)",
          file=sys.stderr)
    rr = result.get("record_result")
    if rr is not None and not rr.get("ok"):
        print(f"gate {args.gate}: result NOT recorded in state.json - "
              f"{rr.get('reason')}", file=sys.stderr)
        return 2
    if cr is not None and not cr.get("ok"):
        print(f"gate {args.gate}: requested commit did not occur - "
              f"{cr.get('reason')}", file=sys.stderr)
        return 2
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
