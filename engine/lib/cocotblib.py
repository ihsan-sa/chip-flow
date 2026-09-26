"""cocotblib.py - shared cocotb/Icarus plumbing for the digital gates that
actually simulate (sim, holdout, mutate - docs/design.md "### M2.").

Not named in docs/design.md 1.3's module list - that list ports /hwde's
KiCad-domain modules and names simlib as the ANALOG equivalent (ngspice
benches). Nothing in the design doc covers "build+run a directory of cocotb
tests over Icarus, parse pass/fail, and tell requirement tags apart from
test bodies" - sim, holdout and mutate all need exactly this, so it lives
here once rather than three times.

Requirement tagging convention (this module's own choice; docs/design.md
section 2 says only "every test carries the requirement ids it covers", not
the mechanism): a `# req: ID [ID2 ...]` comment on the line immediately
before a `@cocotb.test()` decorator tags that test with the requirement
id(s) it covers; several such lines stacked above one test (other comment
lines may sit between them) tag it with the union of their ids - UNLESS that decorator carries `expect_fail`/`expect_error`
(see scan_requirement_tags below): a test cocotb itself expects to fail
cannot silently stand in for a real pass.
"""
from __future__ import annotations

import re
import signal
import xml.etree.ElementTree as ET
from pathlib import Path


class CocotbTimeout(RuntimeError):
    """run_cocotb's build()+test() exceeded its wall-clock budget. Raised,
    never swallowed - a cosim bench wedged inside ngspice's shared library
    has no subprocess to time out on its own (there is no exit code at all,
    docs/spikes/dcosim.md), so without this a hang here hangs the gate
    forever."""

REQ_TAG_RE = re.compile(r"^\s*#\s*req:\s*(.+?)\s*$")
COCOTB_TEST_RE = re.compile(r"^\s*@cocotb\.test\(")
DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(")
# `@cocotb.test(expect_fail=True)` / `expect_error=...`: cocotb's own xunit
# reporter (cocotb/regression.py _record_test_xfail) writes such a test's
# expected failure with NO <failure>/<error>/<skipped> child at all - by
# default (COCOTB_XFAIL_IN_RESULTS off) it is even written with status
# "passed", indistinguishable in results.xml from a real pass. Nothing in
# parse_results_xml below can ever catch that after the fact, so it is
# caught here instead, statically: a test decorated this way never gets its
# `# req:` comment attached, so it can never satisfy required coverage
# (check_sim.py) or be treated as a tagged holdout test (check_holdout.py).
EXPECT_FAILURE_RE = re.compile(r"\bexpect_(?:fail|error)\b")


def scan_requirement_tags(py_dir: Path) -> dict[str, set[str]]:
    """{test_function_name: {req_id, ...}} for every `@cocotb.test()`
    function under py_dir (non-recursive - test files live directly in tb/
    or holdout/) whose immediately preceding non-blank line is a `# req:
    ...` comment, with every stacked `# req:` line in the comment block above
    it counted, not only the last. A test with no such comment is simply absent here; callers
    decide whether that omission is itself a finding.

    A test decorated `expect_fail`/`expect_error` never gets its pending tag
    attached, whatever the decorator's own line span (the lookahead window
    below is the same one already used to find the `def` line, so both
    checks see the same text) - see EXPECT_FAILURE_RE's own comment for why."""
    tags: dict[str, set[str]] = {}
    if not py_dir.is_dir():
        return tags
    for py in sorted(py_dir.glob("*.py")):
        lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
        pending: set[str] | None = None
        for i, line in enumerate(lines):
            m = REQ_TAG_RE.match(line)
            if m:
                # Stacked `# req:` lines above one test all count (ece298a
                # round 2, breakage 11: a second line used to replace the
                # first, silently dropping its ids from coverage).
                pending = (pending or set()) | set(m.group(1).split())
                continue
            if COCOTB_TEST_RE.match(line):
                window = lines[i:i + 4]
                expects_failure = any(EXPECT_FAILURE_RE.search(w) for w in window)
                for look in window:
                    dm = DEF_RE.match(look)
                    if dm:
                        if pending and not expects_failure:
                            tags.setdefault(dm.group(1), set()).update(pending)
                        break
                pending = None
                continue
            if line.strip() and not line.strip().startswith("#"):
                pending = None
    return tags


def test_modules(py_dir: Path) -> list[str]:
    """Module stems (`test_*.py`, cocotb/pytest convention) to hand to
    cocotb's runner as `test_module` - everything else under py_dir (a
    reference model, a shared helper) is support code the tests import, not
    a module cocotb should scan for @cocotb.test() itself."""
    if not py_dir.is_dir():
        return []
    return [p.stem for p in sorted(py_dir.glob("test_*.py"))]


def required_ids(spec: dict, checks: tuple[str, ...] = ("sim", "both")) -> set[str]:
    return {r["id"] for r in (spec.get("requirements") or [])
           if isinstance(r, dict) and r.get("check") in checks and r.get("id")}


def run_cocotb(build_dir: Path, test_dir: Path, sources: list[Path],
              hdl_toplevel: str, test_modules_: list[str], results_xml: Path,
              timescale: tuple[str, str] = ("1ns", "1ps"),
              timeout_s: float | None = None, waves: bool = False) -> Path:
    """Build the design then run every module in test_modules_ as one cocotb
    regression over Icarus. Returns the results.xml path (results_xml is
    pinned explicitly - concurrent callers, e.g. check_mutate.py running one
    mutant per task, must never share cocotb's own default path).

    log_file=build_dir/sim.log is passed to both build() and test(): without
    it, cocotb_tools.runner's own Simulator._execute runs iverilog/vvp with
    stdout=None, which inherits the CALLER's real stdout fd - the same fd a
    caller several layers up (gate.py --gate sim) later prints its JSON
    report to. Every line of iverilog/vvp/cocotb's own console output would
    land on that fd ahead of the JSON, so a consumer that runs gate.py as a
    subprocess and expects pure JSON on stdout gets unparsable noise instead
    (nothing here breaks: results are already read back from results_xml,
    never from this log).

    Every path handed to cocotb_tools.runner is resolved to absolute FIRST
    (found the hard way, M5): the runner's own Simulator._execute changes
    the real OS working directory before it spawns iverilog/vvp, so a
    caller's RELATIVE `results_xml` (or build_dir/test_dir) is no longer
    relative to the caller's own cwd by the time it is actually opened - it
    lands nested one more copy of the workspace's own relative prefix
    beneath test_dir (e.g. `tb/blocks/x/log/results.xml` instead of
    `blocks/x/log/results.xml`). That silently writes INSIDE `tb/` itself,
    a `dir_text`-hashed gate input, and staled every gate that reads `tb`
    on the very next hash check with no RTL/tb edit involved. Absolute
    paths make every one of these calls independent of whatever directory
    the runner is standing in at the moment it opens them.

    timeout_s, if given, bounds build()+test() together with a SIGALRM
    (Unix - matching bin/eda's own platform assumption; None, the default,
    changes nothing for existing callers). This is the only lever available
    for a co-simulation run: build()/test() call straight into cocotb's own
    Python API and ngspice's shared library, not a subprocess this module
    can attach a `timeout=` kwarg to or kill outright, so a real hang here
    (an ngspice shared-library co-sim wedged waiting on a sync point that
    never comes, say) would otherwise block forever with no exit code to
    time out on. CocotbTimeout propagates to the caller uncaught - check_
    *.py's cli_wrap (checklib.py) maps any exception to exit 2, the same
    "this gate did not run" contract a hang should report.

    waves=True has cocotb dump `<build_dir>/<hdl_toplevel>.fst` (Icarus
    always gets -fst or -none, so never VCD); optimise.py's power metric
    is the one caller, and converts it with `eda fst2vcd`."""
    from cocotb_tools.runner import get_runner
    build_dir = Path(build_dir).resolve()
    test_dir = Path(test_dir).resolve()
    sources = [Path(s).resolve() for s in sources]
    results_xml = Path(results_xml).resolve()
    runner = get_runner("icarus")
    log_file = str(build_dir / "sim.log")

    def _on_alarm(signum, frame):
        raise CocotbTimeout(
            f"run_cocotb exceeded its {timeout_s:g}s wall-clock timeout "
            f"(hdl_toplevel={hdl_toplevel!r})")

    old_handler = None
    if timeout_s is not None:
        old_handler = signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(max(1, int(timeout_s)))
    try:
        runner.build(sources=[str(s) for s in sources], hdl_toplevel=hdl_toplevel,
                    build_dir=str(build_dir), waves=waves, timescale=timescale,
                    log_file=log_file)
        try:
            # cocotb_tools.runner.test() itself does sys.exit(1) when any
            # test in the run FAILED (mirroring a CLI tool's own exit code)
            # - by the time it does, results_xml is already written in
            # full, and a failing test is exactly the outcome this gate
            # exists to catch, so that exit is swallowed here and the
            # caller reads results_xml itself rather than trusting a bare
            # return code either way.
            return runner.test(hdl_toplevel=hdl_toplevel, test_module=test_modules_,
                               test_dir=str(test_dir), build_dir=str(build_dir),
                               results_xml=str(results_xml), waves=waves,
                               log_file=log_file)
        except SystemExit:
            return Path(results_xml)
    finally:
        if timeout_s is not None:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)


def parse_results_xml(xml_path: Path) -> dict[str, dict]:
    """{test_function_name: {"passed": bool, "skipped": bool, "message":
    str|None}} from a JUnit-shaped results.xml (cocotb_tools.runner's own
    output format - <testcase name="module.function"> with a
    <failure>/<error>/<skipped> child). Only the function part of `name` is
    kept, matching scan_requirement_tags's keys.

    A <skipped> testcase (cocotb.test(skip=True)) has neither <failure> nor
    <error>, so it used to fall through as "passed" here - a test that never
    ran would count as covering whatever requirement it was tagged with.
    Reported as passed=False, skipped=True instead; callers (check_sim.py,
    check_holdout.py) turn that into a `test_skipped` finding rather than
    `test_failed`/`holdout_failed`, since it never actually exercised
    anything."""
    out: dict[str, dict] = {}
    tree = ET.parse(xml_path)
    for tc in tree.iter("testcase"):
        name = tc.attrib.get("name", "")
        short = name.rsplit(".", 1)[-1]
        skip = tc.find("skipped")
        if skip is not None:
            out[short] = {
                "passed": False,
                "skipped": True,
                "message": (skip.attrib.get("message")
                           or (skip.text or "").strip() or None),
            }
            continue
        bad = tc.find("failure")
        if bad is None:
            bad = tc.find("error")
        out[short] = {
            "passed": bad is None,
            "skipped": False,
            "message": (bad.attrib.get("message") or (bad.text or "").strip()
                       if bad is not None else None),
        }
    return out
