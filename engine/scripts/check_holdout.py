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

A held-out test that dies in its OWN stimulus code is not a design fault:
held-out tests import the visible tb/ helpers, so a helper that changes
shape (returns three values where the test unpacks two) breaks them with
no RTL at fault. Such a failure is `holdout_stimulus_fault`, routed to the
test's writer (who may adapt stimulus and helper calls, and nothing that
judges - check_holdout_edit.py and state.py's `holdout_stimulus_edit`
enforce that), never to the rtl fixer. It is one only when all three hold:
the exception is not an AssertionError; the innermost traceback frame is a
file inside the workspace (holdout/ or tb/), not cocotb or the simulator;
and that frame's line is neither an `assert` nor a `raise` (an `int()` of
an X-valued signal inside an assert raises ValueError from cocotb's own
code, and a tb helper's `raise TimeoutError("no lock")` is a helper judging
the design - both are the design's). Anything else stays `holdout_failed`. The finding names the
exception class only, never the test, its file or its message.

Fault this gate must catch (gates.yaml): "UART parity inverted where the
visible tests do not look" - a bug only a held-out test, not tb/, exercises.
"""
from __future__ import annotations

import re
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
FRAME_RE = re.compile(r'^\s*File "([^"]+)", line \d+, in \S+\s*$')
BUILD_SUBDIR = "log/holdout_build"
RESULTS_NAME = "holdout_results.xml"


def collect_sources(ws: Path) -> list[Path]:
    rtl_dir = ws / "rtl"
    files = sorted(rtl_dir.glob("*.v")) + sorted(rtl_dir.glob("*.sv"))
    if not files:
        raise CheckError(f"no .v/.sv files under {rtl_dir}")
    return files


def stimulus_fault(res: dict, ws: Path) -> str | None:
    """The exception class when a failed held-out test died in its own
    stimulus code (see the module docstring), else None."""
    etype = res.get("type") or ""
    if not etype or etype.rsplit(".", 1)[-1] == "AssertionError":
        return None
    lines = (res.get("traceback") or "").splitlines()
    frames = [(i, m.group(1)) for i, line in enumerate(lines)
              if (m := FRAME_RE.match(line))]
    if not frames:
        return None
    i, path = frames[-1]
    try:
        inside = Path(path).resolve().is_relative_to(ws.resolve())
    except OSError:
        return None
    code = lines[i + 1].strip() if i + 1 < len(lines) else ""
    # an assert or a raise in workspace code is a judgement of the design
    # (check_holdout_edit.py counts a raise as one too), not stimulus
    if not inside or re.match(r"(assert|raise)\b", code):
        return None
    return etype.rsplit(".", 1)[-1]


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
        if res.get("skipped"):
            violations.append(checklib.violation(
                "holdout", "error", None, None, "test_skipped", refs,
                f"a held-out test for requirement(s) {', '.join(refs)} was "
                "skipped - it never ran, so it cannot cover them", "cocotb"))
            continue
        etype = stimulus_fault(res, ws)
        if etype:
            violations.append(checklib.violation(
                "holdout", "error", None, None, "holdout_stimulus_fault",
                refs, f"a held-out test for requirement(s) {', '.join(refs)} "
                f"died in its own stimulus code ({etype}) before any assert "
                "judged the design", "cocotb"))
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
