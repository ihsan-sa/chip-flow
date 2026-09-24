#!/usr/bin/env python
"""check_sim.py - the sim gate (docs/design.md 1.5, "### M2.").

    check_sim.py --workspace DIR [--out FILE]

Builds rtl/ and runs every `tb/test_*.py` module as one cocotb regression
over Icarus (engine/lib/cocotblib.py). Passes when every test passes AND
every requirement whose spec.yaml `check` is `sim` or `both` has at least
one test tagged with it (a `# req: ID` comment above the test - cocotblib's
convention for "every test carries the requirement ids it covers", docs/
design.md section 2).

Runs in-process (this script is always reached through `eda python ...`,
docs/design.md 1.2 - the interpreter already has cocotb/cocotb_tools on its
path and Icarus on PATH via the shim directory, so no bin/eda subprocess is
needed here the way check_lint.py needs one for verilator).

Faults this gate must catch (gates.yaml): "counter wraps one early" (an RTL
bug a real test catches) and, named separately in M2's done criteria, "a
requirement without a test fails sim".
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

SCRIPT = "check_sim"
BUILD_SUBDIR = "log/sim_build"
RESULTS_NAME = "sim_results.xml"


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
    tb_dir = ws / "tb"
    modules = cocotblib.test_modules(tb_dir)
    if not modules:
        raise CheckError(f"no test_*.py modules under {tb_dir}")
    tags = cocotblib.scan_requirement_tags(tb_dir)
    tagged_ids: set[str] = set().union(*tags.values()) if tags else set()
    required = cocotblib.required_ids(spec, checks=("sim", "both"))

    violations = []
    for rid in sorted(required - tagged_ids):
        violations.append(checklib.violation(
            "sim", "error", "spec/spec.yaml", None, "requirement_no_test",
            [rid], f"requirement {rid} has no test tagged with it (a "
            f"'# req: {rid}' comment above the @cocotb.test() that covers "
            "it)", "cocotblib"))

    build_dir = ws / BUILD_SUBDIR
    shutil.rmtree(build_dir, ignore_errors=True)
    results_xml = ws / "log" / RESULTS_NAME
    xml_path = cocotblib.run_cocotb(build_dir, tb_dir, sources, top, modules,
                                    results_xml)
    results = cocotblib.parse_results_xml(xml_path)
    if not results:
        raise CheckError(f"cocotb produced no test cases in {xml_path}")

    for name, res in sorted(results.items()):
        if res["passed"]:
            continue
        refs = sorted(tags.get(name, []))
        violations.append(checklib.violation(
            "sim", "error", None, name, "test_failed", refs,
            f"{name} failed: {res['message'] or 'see the sim log'}",
            "cocotb"))

    # stamp() hashes exactly this path as input_digest; gate.py's own
    # record_gate cross-checks that against invalidation.yaml's gate_inputs
    # kinds[0] for this gate ("rtl" for sim) - the whole workspace would
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
