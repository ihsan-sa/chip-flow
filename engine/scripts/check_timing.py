#!/usr/bin/env python
"""check_timing.py - the timing gate (docs/design.md 1.5, "### M4.").

    check_timing.py --workspace DIR [--out FILE]

Independently re-runs OpenSTA (`eda sta`, never LibreLane's own built-in
timing checker - the design's whole point in separating this from `harden`
is a second, independent signoff on what the hardening tool claims) on the
hardened netlist, once per corner LibreLane actually produced
(`harden/runs/run/final/lib/<corner>/`), linked against the PDK's own
standard-cell liberty for that corner (engine/lib/ttlib.py's
`stdcell_liberty_path` - NOT the per-corner file under that same `lib/`
directory, which is the hardened macro's own abstracted timing view, not
the cell library) with the matching SPEF for parasitics and the flow's own
SDC for constraints.

Passes when every corner's worst setup AND hold slack is >= 0 and
`report_check_types` finds no max-slew/max-cap/max-fanout violator
(gates.yaml's `timing` row). Fault this gate must catch: "a chain that
misses the spec's clock" (docs/design.md 1.5) - a design whose slowest
corner runs the clock period into negative setup slack.

Failure classification (the same three-way split check_harden.py uses):
  - no `harden/runs/run/final/` at all -> CheckError (harden has not run;
    never a pass masquerading as "nothing to check").
  - `eda sta` for a corner times out, crashes, or exits with no parseable
    "worst slack" line, or with no slew/cap/fanout counts line ->
    CheckError (a launcher/tool failure is a refusal, never recorded as a
    plain slack violation the fix loop could waive as "just timing", and
    never a pass on a check that did not run).
  - every corner's STA completes and reports a negative slack or a
    reported violator -> a `violations` finding, exit 1.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import speclib  # noqa: E402
import ttlib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_timing"
EDA_BIN = REPO / "bin" / "eda"
TIMEOUT_S = 120.0
SLACK_RE = re.compile(r"worst slack (max|min)\s+(-?[0-9.]+(?:e-?[0-9]+)?)")
# OpenSTA 3.1.0's `report_check_types -violators` prints a bare section
# header ("max slew", "max capacitance", "max fanout"), a column header and
# a dashed rule, then one row per violating pin ending "(VIOLATED)" - never
# the word "violation" (tests/fixtures/opensta/check_types_violators.txt is
# its real output). The rows are only the finding's detail: the pass/fail
# decision rests on sta's own per-check counters (the same ones LibreLane's
# corner.tcl reads), printed on one marker line the gate insists on seeing.
CHECK_KINDS = {"max slew": "slew", "max capacitance": "cap",
               "max fanout": "fanout"}
COUNTS_MARK = "chipflow_violation_counts"
COUNTS_RE = re.compile(
    rf"^{COUNTS_MARK} slew (\d+) cap (\d+) fanout (\d+)\s*$", re.MULTILINE)
VIOLATED_ROW_RE = re.compile(r"^(\S+)\s.*\(VIOLATED\)\s*$")
COUNTS_TCL = (f'puts "{COUNTS_MARK} slew [sta::max_slew_violation_count] '
              "cap [sta::max_capacitance_violation_count] "
              'fanout [sta::max_fanout_violation_count]"\n')


def parse_check_types(output: str) -> dict:
    """{"slew"|"cap"|"fanout": {"count": n, "pins": [...]}} from one sta
    run's output. `count` is sta's own counter; `pins` the "(VIOLATED)"
    rows listed under that check's section header. No counts line at all
    is a CheckError: a run that never reached it proves nothing clean."""
    m = COUNTS_RE.search(output)
    if not m:
        raise CheckError(f"eda sta printed no {COUNTS_MARK} line - the "
                         "slew/cap/fanout check never ran")
    result = {kind: {"count": int(n), "pins": []}
              for kind, n in zip(("slew", "cap", "fanout"), m.groups())}
    current = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.lower() in CHECK_KINDS:
            current = CHECK_KINDS[stripped.lower()]
            continue
        row = VIOLATED_ROW_RE.match(stripped)
        if row and current:
            result[current]["pins"].append(row.group(1))
    return result


def _pdk_root() -> Path:
    proc = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError(f"eda --print-toolchain-root failed: "
                         f"{proc.stderr.strip()}")
    return Path(proc.stdout.strip()) / "foss" / "pdks"


def run_corner(final_dir: Path, top: str, corner: str, pdk_root: Path,
              work_dir: Path) -> dict:
    bucket = corner.split("_", 1)[0]
    lib = ttlib.stdcell_liberty_path(corner, pdk_root)
    netlist = final_dir / "nl" / f"{top}.nl.v"
    sdc = final_dir / "sdc" / f"{top}.sdc"
    spef = final_dir / "spef" / bucket / f"{top}.{bucket}.spef"
    for label, p in (("liberty", lib), ("netlist", netlist), ("sdc", sdc),
                     ("spef", spef)):
        if not p.is_file():
            raise CheckError(f"corner {corner!r}: no {label} at {p}")

    # written under work_dir (log/timing_work/), never final_dir: final_dir
    # sits inside harden/, the exact directory tree the "harden" artifact
    # kind hashes for freshness (invalidation.yaml) - a scratch file dropped
    # there would change that hash on every timing run and falsely stale
    # every OTHER gate that also reads "harden" (drc, lvs, glsim, precheck,
    # release), including timing's own last-recorded pass.
    tcl = work_dir / f".sta_{corner}.tcl"
    tcl.write_text(
        f"read_liberty {lib}\n"
        f"read_verilog {netlist}\n"
        f"link_design {top}\n"
        f"read_sdc {sdc}\n"
        f"read_spef {spef}\n"
        "report_worst_slack -max\n"
        "report_worst_slack -min\n"
        "report_check_types -max_slew -max_capacitance -max_fanout -violators\n"
        + COUNTS_TCL + "exit\n", encoding="utf-8")
    try:
        proc = subprocess.run([str(EDA_BIN), "sta", str(tcl)], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"corner {corner!r}: eda sta timed out after "
                         f"{TIMEOUT_S:g}s: {exc}") from exc
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    slacks = {kind: float(val) for kind, val in SLACK_RE.findall(output)}
    if "max" not in slacks or "min" not in slacks:
        raise CheckError(
            f"corner {corner!r}: eda sta produced no parseable worst-slack "
            f"line (exit {proc.returncode}): {output[-2000:]}")
    try:
        checks = parse_check_types(output)
    except CheckError as exc:
        raise CheckError(f"corner {corner!r}: {exc} (exit {proc.returncode}): "
                         f"{output[-2000:]}") from exc
    violators = []
    for kind, found in checks.items():
        if found["count"] or found["pins"]:
            pins = found["pins"]
            shown = ", ".join(pins[:5]) + (" ..." if len(pins) > 5 else "")
            violators.append(f"max {kind}: {max(found['count'], len(pins))} "
                             f"violating pin(s){': ' + shown if shown else ''}")
    return {"corner": corner, "setup_ws": slacks["max"], "hold_ws": slacks["min"],
           "violators": violators}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = ttlib.wrapper_name(spec)
    final_dir = ws / "harden" / "runs" / "run" / "final"
    lib_dir = final_dir / "lib"
    if not lib_dir.is_dir():
        raise CheckError(f"no {lib_dir} - the harden gate has not produced "
                         "a hardened design to time yet")
    corners = sorted(p.name for p in lib_dir.iterdir() if p.is_dir())
    if not corners:
        raise CheckError(f"{lib_dir} has no corner subdirectories")

    pdk_root = _pdk_root()
    work_dir = ws / "log" / "timing_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    results = [run_corner(final_dir, top, corner, pdk_root, work_dir)
              for corner in corners]

    violations = []
    for r in results:
        if r["setup_ws"] < 0:
            violations.append(checklib.violation(
                "timing", "error", None, top, "setup_violation", [],
                f"corner {r['corner']}: worst setup slack {r['setup_ws']:.4f}ns",
                "opensta", corner=r["corner"]))
        if r["hold_ws"] < 0:
            violations.append(checklib.violation(
                "timing", "error", None, top, "hold_violation", [],
                f"corner {r['corner']}: worst hold slack {r['hold_ws']:.4f}ns",
                "opensta", corner=r["corner"]))
        for line in r["violators"]:
            violations.append(checklib.violation(
                "timing", "error", None, top, "slew_or_cap_or_fanout_violation",
                [], f"corner {r['corner']}: {line}", "opensta",
                corner=r["corner"]))

    payload = checklib.report(SCRIPT, ws / "harden", violations, top=top,
                              corners={r["corner"]: {"setup_ws": r["setup_ws"],
                                                     "hold_ws": r["hold_ws"]}
                                      for r in results})
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
