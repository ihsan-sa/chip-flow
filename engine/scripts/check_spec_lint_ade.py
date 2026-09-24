#!/usr/bin/env python
"""check_spec_lint_ade.py - /ade's spec_lint gate (docs/design.md 1.5,
"### M8."). A dedicated script rather than a skill-aware check_spec_lint.py:
that script is vde's own (M2), requires a non-empty `requirements` list and
validates `tt_pins`/`clock` - neither of which gates.yaml's ade spec_lint row
asks for ("Every measure has bounds and a corner set; supply and devices
declared."), and reusing it wholesale would force every analog block to also
carry digital-shaped fields it has no use for. Mirrors check_spec_lint.py's
own shape exactly otherwise.

    check_spec_lint_ade.py --workspace DIR [--out FILE]

Fault this gate must catch (gates.yaml): "a measure without bounds".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import checklib  # noqa: E402
import speclib  # noqa: E402

SCRIPT = "check_spec_lint_ade"
SPEC_REL = "spec/spec.yaml"


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec_path = ws / SPEC_REL
    spec = speclib.load_spec(spec_path)
    violations = speclib.lint_spec_ade(spec, rel_path=SPEC_REL)
    # stamp() hashes exactly this path as input_digest; gate.py's own
    # record_gate cross-checks that against invalidation.yaml's gate_inputs
    # kinds[0] for this gate ("spec_yaml" for ade's spec_lint too).
    payload = checklib.report(SCRIPT, spec_path, violations)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
