#!/usr/bin/env python
"""attest.py - the record of what ran (docs/design.md 1.5's `release` gate,
section 2's "The record of what ran is the release").

Ported from /hwde's scripts/attest.py, scoped down for M1: /hwde's
attest.py delegates to lib/releaselib.py (fab package hashes, durable
waiver bindings, a hand-set-proof disposition ladder from draft through
ordered/built) - none of that is in engine/lib/ (docs/design.md 1.1 lists
checklib, safelib, statelib, benchlib, simlib, speclib only) because a
board's fab/order concepts don't port. What attest.py owns is the part that
does: walk every gate gates.yaml registers for the block's skill, check each
has a recorded pass whose input hashes match the files now (statelib
freshness), and write reports/checks.json - gate, tool, result, inputs,
timestamp. M1's gates were all stubs, so build() always refused; M3 ("###
M3.") is where a real release can happen, and where a scoped-down waiver/
durability mechanism lands (load_waivers/waiver_problems below) - scoped
down because a board's fab/order concepts (/hwde releaselib's durable-waiver
required fields beyond reason+approved, its manufacturing-option waivers)
don't port, but the CORE idea does: a waiver binds to the gate's CURRENT
input hashes (freshness_report's current_inputs, re-hashed from the files on
disk every call - never the gate's own last RECORDED inputs, which stay
frozen at record time and so would let a waiver approved on RTL A silently
keep covering the gate after the RTL moves to B with no re-run in between)
and to checklib.CHECKER_VERSION, so an artifact edit or a checker semantics
bump invalidates it and it must be re-approved. A waiver is also refused
outright whenever the gate's own hash_valid is False (its last recorded run
no longer matches the current files): a waiver never covers a gate that has
not actually run against the inputs it is being asked to cover.

Subcommands:
  build        Assemble + write reports/checks.json. Refuses (status
               violations, exit 1, nothing written) unless every applicable
               gate has a fresh recorded pass. The refusal lists every miss
               at once.
  verify       Re-verify the written checks.json against the current tree
               and state. Read-only. Exit 0 valid / 1 invalid.
  disposition  Print the DERIVED release disposition (never hand-set):
               draft (nothing has passed yet), in-progress (some gates
               green), gates-green (every applicable gate fresh-pass),
               blocked (a gate has a recorded FAIL). Read-only.

CLI: JSON to stdout or --out; exit 0 ok / 1 refused-or-invalid /
2 operational error.

  attest.py build --workspace DIR [--max-report-age-h H] [--out FILE]
  attest.py verify --workspace DIR [--out FILE]
  attest.py disposition --workspace DIR [--out FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import checklib  # noqa: E402
import gate as gate_mod  # noqa: E402
import statelib  # noqa: E402

SCRIPT = "attest"
CHECKS_PATH = "reports/checks.json"
WAIVERS_PATH = "reports/waivers.json"
# a waiver missing any of these is not durable - never applied, always a
# problem attest.py's own build() surfaces (docs/design.md 1.5's release
# row: "waivers carry reason, approval and durability").
REQUIRED_WAIVER_FIELDS = ("reason", "approved_by", "inputs_sha256")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_waivers(ws: Path) -> list[dict]:
    """The block's own reports/waivers.json - a JSON list, absent by
    default (no file = no waivers, not an error)."""
    path = ws / WAIVERS_PATH
    if not path.is_file():
        return []
    data = checklib.load_json(path, "waivers.json")
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON list of waivers")
    return data


def waiver_inputs_sha256(current_inputs: dict | None) -> str | None:
    """The durability binding: a canonical hash of the gate's CURRENT input
    hashes (freshness_report's current_inputs for this gate - re-hashed from
    the files on disk every call) - never the gate's own last RECORDED
    inputs (state.json's gates.<g>.last.inputs), which stay frozen at record
    time. Binding to the recorded value let a waiver approved while the RTL
    read A silently keep covering the gate after the RTL moved to B, with no
    re-run in between - entry.last.inputs never moved, so the old hash kept
    matching. Binding to current_inputs instead means the hash itself moves
    the moment a file does; waiver_problems below additionally refuses
    outright whenever hash_valid is False, so a waiver can never be written
    against a hypothetical "current" state the gate has not actually run
    against either."""
    if not isinstance(current_inputs, dict) or not current_inputs:
        return None
    return hashlib.sha256(
        json.dumps(current_inputs, sort_keys=True).encode("utf-8")).hexdigest()


def waiver_problems(w: dict, gate: str, entry: dict, verdict: dict) -> list[str]:
    """Durability problems for one candidate waiver against the gate entry
    it claims to cover - every one raises the release refusal, none of them
    silently drop the waiver back to "no waiver" (a malformed or stale
    waiver is worse than none: it looks like cover for a gate nobody
    actually re-approved). `verdict` is this gate's own statelib.
    gate_freshness verdict (fresh["gates"][gate]) - the same one gate_check
    already computed, so this never re-derives freshness on its own."""
    label = f"{gate}"
    missing = [f for f in REQUIRED_WAIVER_FIELDS if not w.get(f)]
    if missing:
        return [f"waiver [{label}]: not durable - missing "
                f"{'/'.join(missing)} (a release waiver must bind reason, "
                "approval and the gate's own input hashes)"]
    problems = []
    if verdict.get("hash_valid") is False:
        problems.append(
            f"waiver [{label}]: the gate's inputs changed since its last "
            "recorded run (" + ", ".join(verdict.get("changed_inputs") or [])
            + ") - a waiver never covers a gate that has not actually run "
            "against its current inputs; re-run the gate first")
    current = waiver_inputs_sha256(verdict.get("current_inputs"))
    if current is None:
        problems.append(f"waiver [{label}]: the gate has no current input "
                        "hashes to bind against - nothing durable to waive")
    elif w["inputs_sha256"] != current:
        problems.append(f"waiver [{label}]: approved against different "
                        "inputs than the gate's current ones - "
                        "re-approve under the current inputs")
    checker_version = w.get("checker_version")
    if checker_version != checklib.CHECKER_VERSION:
        problems.append(f"waiver [{label}]: approved under checker_version "
                        f"{checker_version!r}, current is "
                        f"{checklib.CHECKER_VERSION} - re-approve")
    expires = w.get("expires")
    if expires:
        from datetime import datetime, timezone
        try:
            exp = datetime.fromisoformat(str(expires))
        except (TypeError, ValueError):
            problems.append(f"waiver [{label}]: unparsable expires {expires!r}")
        else:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                problems.append(f"waiver [{label}]: expired {expires}")
    return problems


def applicable_gates(skill: str, imap: dict | None = None) -> list[str]:
    """Every gate this skill owes - every gates.yaml row for the skill
    applies, unconditionally (no /hwde-style constraints.json
    verification.not_applicable list here: a gate a later milestone has not
    built yet is not "not applicable", it is simply not fresh-passed, and
    build() below refuses on it like any other unbuilt/unrun gate - the one
    way past that for a SPECIFIC, reviewed exception is a durable waiver,
    never a blanket applicability carve-out)."""
    imap = imap or statelib.load_map()
    return sorted((imap["gate_inputs"].get(skill) or {}))


def gate_check(ws: Path, skill: str, gate: str, data: dict,
              imap: dict, fresh: dict, waivers: list[dict] | None = None,
              gates_row: dict | None = None) -> dict:
    """`gates_row` is this gate's own gates.yaml row (build() looks it up
    per-skill) - its `tool` names the check that ran, alongside `version`
    (checklib.CHECKER_VERSION, the checker SEMANTICS version every gate in
    one attestation shares, same one a waiver binds against): "the record
    of what ran is ... gate, tool and version" (docs/design.md section 2)."""
    tool = (gates_row or {}).get("tool")
    version = checklib.CHECKER_VERSION
    entry = (data.get("gates") or {}).get(gate)
    if entry is None or not entry.get("status"):
        return {"gate": gate, "applies": True, "ran": False,
                "tool": tool, "version": version,
                "reason": "no recorded result"}
    verdict = fresh["gates"].get(gate, {})
    ok = entry.get("status") == "pass" and verdict.get("fresh")
    reason = None
    if entry.get("status") != "pass":
        reason = "last recorded result is FAIL"
    elif not verdict.get("fresh"):
        if verdict.get("hash_valid") is False:
            reason = ("stale: input changed since the last pass ("
                      + ", ".join(verdict.get("changed_inputs") or []) + ")")
        elif verdict.get("hash_valid") is None:
            reason = "freshness unknown (no recorded input hashes)"
        else:
            reason = "stale: marked by a later edit"
    result = {"gate": gate, "applies": True, "ran": True, "ok": bool(ok),
             "tool": tool, "version": version,
             "status": entry.get("status"),
             "attempts": entry.get("attempts"),
             "ts": (entry.get("last") or {}).get("ts"),
             "inputs": (entry.get("last") or {}).get("inputs"),
             "reason": reason}
    if ok:
        return result
    # A waiver only ever COVERS a gate that is not itself ok - one on an
    # already-passing gate is inert, never surfaced as a problem (a stale
    # leftover approval for a gate that has since genuinely passed on its
    # own is not this release's concern).
    for w in waivers or []:
        if w.get("gate") != gate:
            continue
        problems = waiver_problems(w, gate, entry, verdict)
        if problems:
            result["waiver_problems"] = problems
            continue
        result["ok"] = True
        result["waived"] = True
        result["waiver"] = {"reason": w["reason"], "approved_by": w["approved_by"]}
        result.pop("waiver_problems", None)
        return result
    return result


def build(ws: Path, max_report_age_h: float = 24.0) -> tuple[dict | None, list[str]]:
    state_path = ws / "state.json"
    data = checklib.load_json(state_path, "state.json")
    skill = data.get("skill")
    if not skill:
        return None, ["state.json has no 'skill'"]
    imap = statelib.load_map()
    fresh = statelib.freshness_report(data, ws, imap)
    skill_rows = gate_mod.load_gates(gate_mod.DEFAULT_GATES).get(skill) or {}
    try:
        waivers = load_waivers(ws)
    except ValueError as exc:
        return None, [str(exc)]
    checks = [gate_check(ws, skill, g, data, imap, fresh, waivers,
                         skill_rows.get(g))
             for g in applicable_gates(skill, imap)]
    problems = []
    for c in checks:
        if c.get("ok"):
            continue
        problems.append(f"{c['gate']}: {c['reason']}")
        problems.extend(c.get("waiver_problems") or [])
    # "escalated" (M5, found running the fix loop for real) is NOT
    # resolved - it is the fix loop's own outcome for a finding a human
    # has to decide on, and release refusing to notice one would be
    # exactly the silent-failure class docs/design.md section 2 exists to
    # answer. Only "fixed"/"waived" are genuinely closed.
    open_issues = [i for i in data.get("open_issues", [])
                   if i.get("status") in ("open", "fixing", "escalated")]
    if open_issues:
        problems.append(f"{len(open_issues)} open issue(s) unresolved")
    if problems:
        return None, problems
    body = {"skill": skill, "block": data.get("block"), "ts": _now(),
           "checks": checks}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    body["attestation_sha256"] = digest
    return body, []


def write_attestation(ws: Path, attestation: dict) -> Path:
    path = ws / CHECKS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(attestation, indent=1), encoding="utf-8")
    return path


def verify(ws: Path) -> dict:
    path = ws / CHECKS_PATH
    if not path.is_file():
        return {"valid": False, "reason": f"no {CHECKS_PATH}"}
    recorded = checklib.load_json(path, "checks.json")
    body = {k: v for k, v in recorded.items() if k != "attestation_sha256"}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    if digest != recorded.get("attestation_sha256"):
        return {"valid": False, "reason": "attestation_sha256 mismatch - "
                "the file was edited after it was written"}
    att, problems = build(ws)
    if att is None:
        return {"valid": False, "reason": "; ".join(problems),
                "attestation_sha256": recorded["attestation_sha256"]}
    if att["checks"] != recorded["checks"]:
        return {"valid": False,
                "reason": "recorded checks no longer match the current "
                          "gate results - re-run `attest build`",
                "attestation_sha256": recorded["attestation_sha256"]}
    return {"valid": True, "attestation_sha256": recorded["attestation_sha256"]}


def disposition(ws: Path) -> dict:
    """The derived release disposition. Never hand-set: draft (nothing has
    passed), in-progress (some applicable gates green), gates-green (every
    applicable gate fresh-pass), blocked (a gate's last recorded result is
    FAIL)."""
    state_path = ws / "state.json"
    if not state_path.is_file():
        return {"disposition": "draft", "reason": "no state.json"}
    data = checklib.load_json(state_path, "state.json")
    skill = data.get("skill")
    if not skill:
        return {"disposition": "draft", "reason": "state.json has no skill"}
    imap = statelib.load_map()
    fresh = statelib.freshness_report(data, ws, imap)
    checks = [gate_check(ws, skill, g, data, imap, fresh)
             for g in applicable_gates(skill, imap)]
    if any(c.get("status") == "fail" for c in checks):
        return {"disposition": "blocked",
                "failing": [c["gate"] for c in checks if c.get("status") == "fail"]}
    if all(c.get("ok") for c in checks):
        return {"disposition": "gates-green", "gates": [c["gate"] for c in checks]}
    if any(c.get("ran") for c in checks):
        return {"disposition": "in-progress",
                "gates_passed": [c["gate"] for c in checks if c.get("ok")]}
    return {"disposition": "draft", "reason": "no gate has run yet"}


def run(argv=None) -> tuple[dict, str | None, int]:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--workspace", required=True,
                       help="block workspace (state.json inside)")
        p.add_argument("--out", help="write result JSON here too")

    p = sub.add_parser("build", help="assemble + write reports/checks.json")
    common(p)
    p.add_argument("--max-report-age-h", type=float, default=24.0)

    common(sub.add_parser("verify", help="re-verify checks.json (read-only)"))
    common(sub.add_parser("disposition",
                          help="derived release disposition (read-only)"))

    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    result: dict = {"script": SCRIPT, "cmd": args.cmd,
                    "workspace": str(ws).replace("\\", "/")}

    if args.cmd == "build":
        att, problems = build(ws, max_report_age_h=args.max_report_age_h)
        if att is None:
            result.update(status="violations", problems=problems,
                          attestation=None)
            return result, args.out, 1
        path = write_attestation(ws, att)
        result.update(status="pass", attestation=str(path).replace("\\", "/"),
                      attestation_sha256=att["attestation_sha256"],
                      disposition=disposition(ws))
        return result, args.out, 0

    if args.cmd == "verify":
        v = verify(ws)
        result.update(status="pass" if v["valid"] else "violations", **v)
        return result, args.out, 0 if v["valid"] else 1

    result.update(status="pass", **disposition(ws))
    return result, args.out, 0


def main(argv=None) -> int:
    checklib.utf8_stdout()
    try:
        payload, out, code = run(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001  (any error -> exit 2)
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}",
                          "remediation": str(exc)}))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
