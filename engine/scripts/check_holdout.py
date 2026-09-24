#!/usr/bin/env python
"""check_holdout.py - the holdout gate (docs/design.md 1.5, "### M2.").

    check_holdout.py --workspace DIR [--out FILE]

Builds rtl/ and runs every `holdout/test_*.py` module as one cocotb
regression over Icarus (engine/lib/cocotblib.py, same mechanism check_sim.py
uses over tb/). Passes when every held-out test passes.

"The result names requirement ids only" (gates.yaml): every held-out test
MUST carry a `# req: ID` tag (cocotblib's convention), and a failure is
reported by that id alone - never the test's name, file or message, which
would hand a fixer the held-out test's own hidden expectation (docs/design.md
section 2: "a held-out failure produces a work order naming the requirement
id ... never the held-out test"). An untagged holdout test can be named by
FILE (which file lacks a tag is a structural fact about the corpus, not the
test's hidden behavior) but never by its content.

Fault this gate must catch (gates.yaml): "UART parity inverted where the
visible tests do not look" - a bug only a held-out test, not tb/, exercises.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import cocotblib  # noqa: E402
import speclib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_holdout"
BUILD_SUBDIR = "log/holdout_build"
RESULTS_NAME = "holdout_results.xml"


def collect_sources(ws: Path) -> list[Path]:
    rtl_dir = ws / "rtl"
    files = sorted(rtl_dir.glob("*.v")) + sorted(rtl_dir.glob("*.sv"))
    if not files:
        raise CheckError(f"no .v/.sv files under {rtl_dir}")
    return files


def run(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = spec.get("top")
    if not isinstance(top, str) or not top.strip():
        raise CheckError("spec.yaml has no non-empty 'top' - run spec_lint "
                         "first")
    sources = collect_sources(ws)
    holdout_dir = ws / "holdout"
    modules = cocotblib.test_modules(holdout_dir)
    if not modules:
        raise CheckError(f"no test_*.py modules under {holdout_dir}")
    tags = cocotblib.scan_requirement_tags(holdout_dir)

    build_dir = ws / BUILD_SUBDIR
    shutil.rmtree(build_dir, ignore_errors=True)
    results_xml = ws / "log" / RESULTS_NAME
    xml_path = cocotblib.run_cocotb(build_dir, holdout_dir, sources, top,
                                    modules, results_xml)
    results = cocotblib.parse_results_xml(xml_path)
    if not results:
        raise CheckError(f"cocotb produced no test cases in {xml_path}")

    violations = []
    for name, res in sorted(results.items()):
        refs = sorted(tags.get(name, []))
        if not refs:
            # structural: which FILE lacks a tag is not the held-out
            # expectation itself, so this alone may name it.
            violations.append(checklib.violation(
                "holdout", "error", None, None, "untagged_holdout_test", [],
                "a held-out test has no '# req: ID' tag - its result cannot "
                "be reported by requirement id alone", "cocotblib"))
            continue
        if res["passed"]:
            continue
        violations.append(checklib.violation(
            "holdout", "error", None, None, "holdout_failed", refs,
            f"a held-out test for requirement(s) {', '.join(refs)} failed",
            "cocotb"))

    # stamp() hashes exactly this path as input_digest; gate.py's own
    # record_gate cross-checks that against invalidation.yaml's gate_inputs
    # kinds[0] for this gate ("rtl" for holdout) - the whole workspace would
    # never match that and silently fail every real recording.
    payload = checklib.report(SCRIPT, ws / "rtl", violations, top=top,
                              tests_run=sorted(results),
                              tests_passed=sum(1 for r in results.values()
                                               if r["passed"]))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
