#!/usr/bin/env python
"""check_cover.py - the cover gate (docs/design.md 1.5, "### M3.").

    check_cover.py --workspace DIR [--out FILE]

Runs the SAME cocotb tests `sim` runs (tb/test_*.py), but over the VERILATOR
simulator instead of Icarus, built with `--coverage --coverage-line
--coverage-toggle`. `verilator_coverage --write-info` (an lcov-shaped .info:
`DA:<line>,<count>` for line hits, `BRDA:<line>,<branch>,<label>,<count>`
for both real branches AND verilator's own toggle points - the two are told
apart here by the label's own shape: a toggle label always ends
`<signal>:0->1`/`<signal>:1->0`, a real branch label never does) is the
parsed source, not the raw `coverage.dat` (a packed, escaped key format not
meant for hand-parsing) and not `--annotate`'s text report (a rendering,
not data).

Two things this gate had to prove empirically before trusting either
number, both now load-bearing here:
  - `bin/eda python3 ...` did not put verilator's own driver script on PATH
    at all (only `eda verilator ...`'s top-level dispatch did) until this
    milestone's fix to bin/eda's `python3|python)` case - a cocotb-over-
    verilator run from inside a check script is new at M3, nothing before
    it needed this path.
  - `eda verilator_coverage ...` used to always exec the `verilator`
    COMPILER driver regardless of which of the three names
    (verilator|verilator_coverage|verilator_gantt) was asked for - a latent
    bug in bin/eda's verilator_entry/dispatch (verilator_coverage is its
    own perl driver, with its own dbg binary, not a flag to `verilator`
    itself) that this milestone's fix also closes, for the same reason: no
    gate needed the real tool before this one.

Pass criteria (gates.yaml `cover` row): line coverage >= `line_min` (spec.yaml
`cover.line_min`, default 95%), toggle coverage >= `cover.toggle_min`
(default 90%), restricted to the DUT's own rtl/ file(s) - a testbench or
cocotb support file is never instrumented in the first place (only rtl/
sources are handed to `--coverage`), so no extra filtering is needed for
that half; multiple rtl/ files are each counted by their own `SF:` block.
`rtl/cover_exclude.yaml` (optional; a list of `{file, line, reason}` for a
line and/or `{file, line, label, reason}` for one toggle edge) removes
exactly the named point from both the covered and total counts - "exclusions
carry reasons" (gates.yaml) mirrors check_lint.py's own `lint_allow.yaml`.

Fault this gate must catch (gates.yaml): "an unreachable state" - a branch
or line the visible tb/ suite never reaches, dragging line coverage below
`line_min`.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import cocotblib  # noqa: E402
import speclib  # noqa: E402
from checklib import CheckError  # noqa: E402

import yaml  # noqa: E402

SCRIPT = "check_cover"
EDA_BIN = REPO / "bin" / "eda"
BUILD_SUBDIR = "log/cover_build"
RESULTS_NAME = "cover_results.xml"
INFO_NAME = "log/coverage.info"
EXCLUDE_REL = "rtl/cover_exclude.yaml"
DEFAULT_LINE_MIN = 95.0
DEFAULT_TOGGLE_MIN = 90.0
TIMEOUT_S = 240.0

TOGGLE_LABEL_RE = re.compile(r":[01]->[01]$")
DA_RE = re.compile(r"^DA:(\d+),(-?\d+)")
BRDA_RE = re.compile(r"^BRDA:(\d+),(\d+),([^,]+),(-|\d+)")
SF_RE = re.compile(r"^SF:(.+)$")


def collect_sources(ws: Path) -> list[Path]:
    rtl_dir = ws / "rtl"
    files = sorted(rtl_dir.glob("*.v")) + sorted(rtl_dir.glob("*.sv"))
    if not files:
        raise CheckError(f"no .v/.sv files under {rtl_dir}")
    return files


def load_excludes(ws: Path) -> dict[str, dict]:
    """{'line': {(file, line): reason}, 'toggle': {(file, line, label): reason}}"""
    out = {"line": {}, "toggle": {}}
    p = ws / EXCLUDE_REL
    if not p.is_file():
        return out
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise CheckError(f"{p} must be a YAML list of exclusions")
    for i, entry in enumerate(data):
        if not isinstance(entry, dict) or not entry.get("reason") \
                or not entry.get("file") or not entry.get("line"):
            raise CheckError(f"{p}[{i}] must have 'file', 'line' and 'reason'")
        key_file, line = entry["file"], int(entry["line"])
        if entry.get("label"):
            out["toggle"][(key_file, line, entry["label"])] = entry["reason"]
        else:
            out["line"][(key_file, line)] = entry["reason"]
    return out


def run_coverage(ws: Path, top: str, sources: list[Path]) -> Path:
    """cocotb over the verilator simulator, --coverage instrumented.
    `runner.test()`'s own `test_dir` is where the compiled simulation
    binary actually RUNS (proved empirically - not `ws`, whatever cwd this
    script itself was launched from, or `build_dir`), so `coverage.dat` (a
    runtime artifact verilator writes to its OWN process cwd, never a path
    you hand it) lands under tb_dir, not ws. It is always removed again
    before returning, success or failure: tb_dir is `dir_text`-hashed as
    the `tb` artifact kind (invalidation.yaml) for sim/mutate/holdout too,
    and a stray coverage.dat left sitting in tb/ would flip THEIR recorded
    input hash on every cover run, marking them stale for a reason that has
    nothing to do with the design under test."""
    from cocotb_tools.runner import get_runner

    build_dir = ws / BUILD_SUBDIR
    shutil.rmtree(build_dir, ignore_errors=True)
    tb_dir = ws / "tb"
    modules = cocotblib.test_modules(tb_dir)
    if not modules:
        raise CheckError(f"no test_*.py modules under {tb_dir}")

    coverage_dat = tb_dir / "coverage.dat"
    coverage_dat.unlink(missing_ok=True)
    # log_file=, on BOTH calls: without it cocotb_tools.runner's own
    # Simulator._execute runs verilator/the compiled sim binary with
    # stdout=None, inheriting THIS script's own stdout fd - the same fd
    # checklib.emit prints the JSON report to (cocotblib.run_cocotb's own
    # docstring names this exact failure mode for check_sim.py/check_
    # holdout.py; this gate is not exempt just because it drives verilator
    # instead of Icarus).
    log_file = str(build_dir / "cover.log")
    try:
        runner = get_runner("verilator")
        try:
            runner.build(sources=[str(s) for s in sources], hdl_toplevel=top,
                        build_dir=str(build_dir), always=True,
                        build_args=["--coverage", "--coverage-line",
                                    "--coverage-toggle"], log_file=log_file)
            try:
                runner.test(hdl_toplevel=top, test_module=modules,
                           test_dir=str(tb_dir), build_dir=str(build_dir),
                           results_xml=str(ws / "log" / RESULTS_NAME),
                           log_file=log_file)
            except SystemExit:
                pass  # a failing test still writes coverage.dat; sim
                      # already failed separately via the `sim` gate.
        except Exception as exc:  # noqa: BLE001 - a build/launcher crash
            raise CheckError(f"cocotb-over-verilator coverage run failed: "
                             f"{type(exc).__name__}: {exc}") from exc

        if not coverage_dat.is_file():
            raise CheckError(f"no {coverage_dat} - the coverage-"
                             "instrumented run never wrote one (a crashed "
                             "launcher or a build that produced no "
                             "coverage binary)")
        return write_coverage_info(ws, coverage_dat)
    finally:
        coverage_dat.unlink(missing_ok=True)


def write_coverage_info(ws: Path, coverage_dat: Path) -> Path:
    """`verilator_coverage --write-info` on its own - split out from
    run_coverage() so a launcher failure here (bin/eda missing, exit 127,
    EDA_TOOLCHAIN pointed nowhere) is testable without a real cocotb+
    verilator build in front of it."""
    info_path = ws / INFO_NAME
    info_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(EDA_BIN), "verilator_coverage", "--write-info",
         str(info_path), str(coverage_dat)],
        cwd=str(ws), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=TIMEOUT_S)
    if proc.returncode != 0 or not info_path.is_file():
        raise CheckError(
            f"verilator_coverage --write-info exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout)[-2000:]}")
    return info_path


def parse_info(info_path: Path) -> dict[str, dict]:
    """{source_file: {'line': {lineno: count}, 'toggle': {(lineno, label): count}}}"""
    out: dict[str, dict] = {}
    cur = None
    for raw in info_path.read_text(encoding="utf-8").splitlines():
        m = SF_RE.match(raw)
        if m:
            cur = out.setdefault(m.group(1), {"line": {}, "toggle": {}})
            continue
        if cur is None:
            continue
        m = DA_RE.match(raw)
        if m:
            cur["line"][int(m.group(1))] = int(m.group(2))
            continue
        m = BRDA_RE.match(raw)
        if m and TOGGLE_LABEL_RE.search(m.group(3)):
            count = 0 if m.group(4) == "-" else int(m.group(4))
            cur["toggle"][(int(m.group(1)), m.group(3))] = count
    if not out:
        raise CheckError(f"{info_path} carries no SF:/DA: records - "
                         "verilator_coverage produced an empty report")
    return out


def run(argv=None):
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
    cover_cfg = spec.get("cover") or {}
    line_min = float(cover_cfg.get("line_min", DEFAULT_LINE_MIN))
    toggle_min = float(cover_cfg.get("toggle_min", DEFAULT_TOGGLE_MIN))
    excludes = load_excludes(ws)

    info_path = run_coverage(ws, top, sources)
    per_file = parse_info(info_path)
    rtl_names = {f.name for f in sources}

    violations, counts = count_coverage(per_file, rtl_names, excludes,
                                        line_min, toggle_min)

    payload = checklib.report(
        SCRIPT, ws / "rtl", violations, top=top,
        line_pct=checklib.rnd(counts["line_pct"]),
        toggle_pct=checklib.rnd(counts["toggle_pct"]),
        line_covered=counts["line_covered"], line_total=counts["line_total"],
        toggle_covered=counts["toggle_covered"],
        toggle_total=counts["toggle_total"])
    return payload, args.out


def count_coverage(per_file: dict[str, dict], rtl_names: set[str],
                   excludes: dict[str, dict], line_min: float,
                   toggle_min: float) -> tuple[list[dict], dict]:
    """The aggregation logic on its own - a pure function over parse_info()'s
    own output, so the exclusion/threshold math is testable without a real
    verilator+cocotb build (that build is what makes this gate's own tests
    slow; tests/check.sh has a two-minute budget, docs/design.md 1.1, and
    this is the part of the gate that does NOT need a real toolchain run to
    exercise)."""
    line_total = line_covered = toggle_total = toggle_covered = 0
    violations = []
    for sf, data in per_file.items():
        name = Path(sf).name
        if name not in rtl_names:
            continue  # cocotb/verilator support code, not the DUT itself
        for lineno, count in sorted(data["line"].items()):
            reason = excludes["line"].get((name, lineno))
            if reason:
                continue
            line_total += 1
            if count > 0:
                line_covered += 1
            else:
                # "info", not "error": gates.yaml's own pass criteria is a
                # PERCENTAGE floor ("line at or above 95%"), not zero misses
                # allowed - a single miss is visible here (and would need
                # rtl/cover_exclude.yaml's own reason to stop showing up at
                # all) but only the aggregate threshold below fails the gate.
                violations.append(checklib.violation(
                    "cover", "info", name, None, "line_not_covered", [],
                    f"{name}:{lineno} was never executed by tb/", "verilator",
                    line=lineno))
        for (lineno, label), count in sorted(data["toggle"].items()):
            reason = excludes["toggle"].get((name, lineno, label))
            if reason:
                continue
            toggle_total += 1
            if count > 0:
                toggle_covered += 1
            else:
                violations.append(checklib.violation(
                    "cover", "info", name, None, "toggle_not_covered", [],
                    f"{name}:{lineno} {label} never toggled", "verilator",
                    line=lineno))

    if line_total == 0:
        raise CheckError("no line-coverage points found for the DUT's own "
                         "rtl/ file(s) - an empty coverage report is a "
                         "refusal, never a pass")
    line_pct = 100.0 * line_covered / line_total
    toggle_pct = 100.0 * toggle_covered / toggle_total if toggle_total else 100.0

    if line_pct < line_min:
        violations.append(checklib.violation(
            "cover", "error", None, None, "line_coverage_below_threshold", [],
            f"line coverage {line_pct:.1f}% ({line_covered}/{line_total}) is "
            f"below {line_min:g}%", "verilator"))
    if toggle_pct < toggle_min:
        violations.append(checklib.violation(
            "cover", "error", None, None, "toggle_coverage_below_threshold", [],
            f"toggle coverage {toggle_pct:.1f}% ({toggle_covered}/"
            f"{toggle_total}) is below {toggle_min:g}%", "verilator"))

    return violations, {
        "line_pct": line_pct, "toggle_pct": toggle_pct,
        "line_covered": line_covered, "line_total": line_total,
        "toggle_covered": toggle_covered, "toggle_total": toggle_total,
    }


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
