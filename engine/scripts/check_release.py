#!/usr/bin/env python
"""check_release.py - the release gate (docs/design.md 1.5, "### M3.").

    check_release.py --workspace DIR [--out FILE]

gates.yaml's `release` row names its tool "attest, strict" (docs/design.md
1.5's table) - this script is the check_<tool>.py wrapper gate.py's generic
dispatch (run_report_for_gate: import check_<tool>, call run(argv), expect
checklib's report shape) needs in order to run it exactly like every other
gate. The actual strict-release-coverage logic - walk every gate gates.yaml
registers for the skill, refuse unless each has a fresh recorded pass or a
durable waiver - already lives in attest.py's own build(); this script calls
it directly and reshapes its "problems" (plain strings) into checklib
violations so gate.py's evaluate() can apply gates.yaml's fail_severities/
max_count the same way it does for every other gate's findings.

"A gate that did not run is a refusal, never a pass" applies one level up
here too: attest.py's build() itself already refuses (returns problems, not
an attestation) for ANY gate with no recorded result, a stale one, or a
recorded FAIL - there is no path through this script that reports "pass"
without attest.py's own build() having produced a real attestation body.

On success, writes reports/checks.json (attest.py's write_attestation) -
docs/design.md section 2's "The record of what ran is the release."
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import attest as attest_mod  # noqa: E402
import statelib  # noqa: E402

SCRIPT = "check_release"


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--max-report-age-h", type=float, default=24.0)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    att, problems = attest_mod.build(ws, max_report_age_h=args.max_report_age_h)
    violations = [
        checklib.violation("release", "error", None, p.split(":", 1)[0]
                           if ":" in p else None, "gate_not_ready", [], p,
                           "attest")
        for p in problems
    ]

    facts: dict = {}
    if att is not None:
        path = attest_mod.write_attestation(ws, att)
        waived = sorted(c["gate"] for c in att["checks"] if c.get("waived"))
        facts = {
            "attestation": str(path.relative_to(ws)),
            "attestation_sha256": att["attestation_sha256"],
            "disposition": attest_mod.disposition(ws)["disposition"],
            "waived": waived,
        }

    # stamp() hashes exactly this path as input_digest; gate.py's own
    # record_gate cross-checks that against invalidation.yaml's gate_inputs
    # kinds[0] for this skill's release row - "rtl" for vde, "netlist" for
    # ade - so the path comes from that map, never a hardcoded "rtl" (an
    # /ade block has no rtl/, and its release pass would never record).
    payload = checklib.report(SCRIPT, primary_input(ws), violations, **facts)
    return payload, args.out


def primary_input(ws: Path) -> Path:
    data = checklib.load_json(ws / "state.json", "state.json")
    imap = statelib.load_map()
    kinds = (imap["gate_inputs"].get(data.get("skill")) or {}).get("release")
    if not kinds:
        raise checklib.CheckError(
            f"invalidation.yaml has no gate_inputs.{data.get('skill')}."
            "release - a release with no input hashes is not evidence")
    return ws / statelib.kind_path(kinds[0], imap, data.get("artifacts"))


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
