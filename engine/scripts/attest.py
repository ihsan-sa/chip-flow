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
timestamp. M1's gates are all stubs, so build() always refuses here; M3
("### M3.") is where a real release can happen, and where the waiver/
durability machinery /hwde's releaselib carried lands.

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
import statelib  # noqa: E402

SCRIPT = "attest"
CHECKS_PATH = "reports/checks.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def applicable_gates(skill: str, imap: dict | None = None) -> list[str]:
    """Every gate this skill owes - M1 has no not-applicable declarations
    (that is /hwde's constraints.json verification.not_applicable, which
    needs the waiver machinery M3 ports), so every gates.yaml row for the
    skill applies."""
    imap = imap or statelib.load_map()
    return sorted((imap["gate_inputs"].get(skill) or {}))


def gate_check(ws: Path, skill: str, gate: str, data: dict,
              imap: dict, fresh: dict) -> dict:
    entry = (data.get("gates") or {}).get(gate)
    if entry is None or not entry.get("status"):
        return {"gate": gate, "applies": True, "ran": False,
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
    return {"gate": gate, "applies": True, "ran": True, "ok": bool(ok),
            "status": entry.get("status"),
            "attempts": entry.get("attempts"),
            "ts": (entry.get("last") or {}).get("ts"),
            "inputs": (entry.get("last") or {}).get("inputs"),
            "reason": reason}


def build(ws: Path, max_report_age_h: float = 24.0) -> tuple[dict | None, list[str]]:
    state_path = ws / "state.json"
    data = checklib.load_json(state_path, "state.json")
    skill = data.get("skill")
    if not skill:
        return None, ["state.json has no 'skill'"]
    imap = statelib.load_map()
    fresh = statelib.freshness_report(data, ws, imap)
    checks = [gate_check(ws, skill, g, data, imap, fresh)
             for g in applicable_gates(skill, imap)]
    problems = [f"{c['gate']}: {c['reason']}" for c in checks if not c.get("ok")]
    open_issues = [i for i in data.get("open_issues", [])
                   if i.get("status") in ("open", "fixing")]
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
