#!/usr/bin/env python
"""check_spec_lint.py - the spec_lint gate (docs/design.md 1.5, "### M2.").

    check_spec_lint.py --workspace DIR [--out FILE]

Reads <workspace>/spec/spec.yaml (docs/design.md 1.4's artifact_kinds:
spec_yaml) and runs engine/lib/speclib.py's lint_spec over it. Passes when
every requirement has an id, a non-empty text and a valid check kind, and
(check: measure) a bounds. Fault this gate must catch (gates.yaml): "a
requirement with no way to check it".

CLI/exit contract: checklib's (argparse, JSON to stdout or --out, exit 0
pass, 1 violations, 2 error with a remediation string).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import checklib  # noqa: E402
import speclib  # noqa: E402

SCRIPT = "check_spec_lint"
SPEC_REL = "spec/spec.yaml"


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec_path = ws / SPEC_REL
    spec = speclib.load_spec(spec_path)
    violations = speclib.lint_spec(spec, rel_path=SPEC_REL)
    payload = checklib.report(SCRIPT, ws, violations)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
