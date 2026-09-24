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
    "worst slack" line -> CheckError (a launcher/tool failure is a refusal,
    never recorded as a plain slack violation the fix loop could waive as
    "just timing").
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
VIOLATOR_RE = re.compile(
    r"^(max slew|max capacitance|max fanout) violation", re.IGNORECASE)


def _pdk_root() -> Path:
    proc = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError(f"eda --print-toolchain-root failed: "
                         f"{proc.stderr.strip()}")
    return Path(proc.stdout.strip()) / "foss" / "pdks"


def run_corner(final_dir: Path, top: str, corner: str, pdk_root: Path) -> dict:
    bucket = corner.split("_", 1)[0]
    lib = ttlib.stdcell_liberty_path(corner, pdk_root)
    netlist = final_dir / "nl" / f"{top}.nl.v"
    sdc = final_dir / "sdc" / f"{top}.sdc"
    spef = final_dir / "spef" / bucket / f"{top}.{bucket}.spef"
    for label, p in (("liberty", lib), ("netlist", netlist), ("sdc", sdc),
                     ("spef", spef)):
        if not p.is_file():
            raise CheckError(f"corner {corner!r}: no {label} at {p}")

    tcl = final_dir / f".sta_{corner}.tcl"
    tcl.write_text(
        f"read_liberty {lib}\n"
        f"read_verilog {netlist}\n"
        f"link_design {top}\n"
        f"read_sdc {sdc}\n"
        f"read_spef {spef}\n"
        "report_worst_slack -max\n"
        "report_worst_slack -min\n"
        "report_check_types -max_slew -max_capacitance -max_fanout -violators\n"
        "exit\n", encoding="utf-8")
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
    violators = [ln.strip() for ln in output.splitlines()
                if VIOLATOR_RE.match(ln.strip())]
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
    results = [run_corner(final_dir, top, corner, pdk_root) for corner in corners]

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
