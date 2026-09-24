#!/usr/bin/env python
"""check_drc.py - the drc gate (docs/design.md 1.5, "### M4.").

    check_drc.py --workspace DIR [--out FILE]

Independently re-runs BOTH magic DRC and klayout's gf180mcu.drc deck (`eda
magic` / `eda klayout`, docs/design.md 1.2) on the hardened GDS
(`harden/runs/run/final/gds/*.gds`) - the vendored template's own
`config.json` turns LibreLane's built-in `RUN_KLAYOUT_DRC` off ("Save some
time"), so this gate is the only DRC signoff this design gets, matching the
`timing`/`lvs` gates' reasoning: a check the hardening tool's own flow
skipped or self-reports is not evidence.

Passes when both tools report 0 violations (gates.yaml's `drc` row). Fault
this gate must catch: "a metal spacing violation planted in the GDS" -
planted directly into the hardened GDS with klayout's python module, per
`corpus/vde/*/faults/plant_drc.py`.

Failure classification: no hardened GDS yet -> CheckError. Either tool
timing out, crashing, or producing no parseable violation count (magic:
no "Total DRC errors found:" line; klayout: no report database file at
all) -> CheckError - a DRC run that did not finish is a refusal, never a
clean 0. A parsed, nonzero violation count from either tool -> a
`violations` finding, exit 1.
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

SCRIPT = "check_drc"
EDA_BIN = REPO / "bin" / "eda"
TIMEOUT_S = 300.0
MAGIC_COUNT_RE = re.compile(r"\[INFO\]: COUNT:\s*(\d+)")


def _pdk_root() -> Path:
    proc = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError(f"eda --print-toolchain-root failed: "
                         f"{proc.stderr.strip()}")
    return Path(proc.stdout.strip()) / "foss" / "pdks"


def run_magic_drc(gds: Path, top: str, workdir: Path) -> int:
    # The full-chip DRC recipe (expand before drc check, euclidean/full
    # style, `drc listall why` for the actual violation list) is the
    # vendored precheck's own magic_drc.tcl (engine/reference/tt/
    # tt-support-tools/precheck/magic_drc.tcl) - a tiny "gds read; select
    # top cell; drc check; drc catchup" script (tests/check.sh's own smoke,
    # a one-box synthetic GDS) never prints a "Total DRC errors found:"
    # line on a real, thousand-instance hardened design; this does.
    #
    # `workdir` is a scratch dir under ws/log/, never final_dir: final_dir
    # sits inside harden/, the exact directory tree the "harden" artifact
    # kind hashes for freshness (invalidation.yaml) - a scratch file dropped
    # there would change that hash on every drc run and falsely stale every
    # OTHER gate that also reads "harden" (timing, lvs, glsim, precheck,
    # release), including drc's own last-recorded pass.
    script = workdir / ".magic_drc.tcl"
    report = workdir / ".magic_drc.rpt"
    script.write_text(
        "gds maskhints yes\n"
        f"gds read {gds}\n"
        f"load {top}\n"
        "select top cell\n"
        "expand\n"
        "drc euclidean on\n"
        "drc style drc(full)\n"
        "drc check\n"
        "set drc_result [drc listall why]\n"
        "set count 0\n"
        "foreach {errtype coordlist} $drc_result {\n"
        "  foreach coord $coordlist { incr count }\n"
        "}\n"
        f'set fout [open {report} w]\n'
        'puts $fout "\\[INFO\\]: COUNT: $count"\n'
        "close $fout\n"
        'puts stdout "\\[INFO\\]: COUNT: $count"\n'
        "flush stdout\n"
        "quit -noprompt\n", encoding="utf-8")
    try:
        proc = subprocess.run([str(EDA_BIN), "magic", str(script)],
                              cwd=str(workdir), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"eda magic DRC timed out after {TIMEOUT_S:g}s: {exc}") from exc
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    m = MAGIC_COUNT_RE.search(output) or (
        MAGIC_COUNT_RE.search(report.read_text(encoding="utf-8"))
        if report.is_file() else None)
    if not m:
        raise CheckError(
            f"magic DRC never reported a violation count (exit "
            f"{proc.returncode}): {output[-2000:]}")
    return int(m.group(1))


def run_klayout_drc(gds: Path, top: str, pdk_root: Path, workdir: Path) -> int:
    deck = pdk_root / ttlib.PDK_NAME / "libs.tech" / "klayout" / "tech" / "drc" / "gf180mcu.drc"
    if not deck.is_file():
        raise CheckError(f"no gf180mcu klayout DRC deck at {deck}")
    report = workdir / ".klayout_drc.lyrdb"
    report.unlink(missing_ok=True)
    cmd = [str(EDA_BIN), "klayout", "-b", "-r", str(deck),
          "-rd", f"input={gds}", "-rd", f"topcell={top}",
          "-rd", "variant=gf180mcuD", "-rd", "run_mode=deep",
          "-rd", "threads=1", "-rd", "decks=all,-density,-antenna",
          "-rd", f"report={report}"]
    try:
        proc = subprocess.run(cmd, cwd=str(workdir), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"eda klayout DRC timed out after {TIMEOUT_S:g}s: {exc}") from exc
    if not report.is_file():
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        raise CheckError(
            f"klayout DRC produced no report database (exit {proc.returncode}): "
            f"{output[-2000:]}")
    import klayout.rdb as rdb
    db = rdb.ReportDatabase("DRC")
    db.load(str(report))
    return db.num_items()


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = ttlib.wrapper_name(spec)
    final_dir = ws / "harden" / "runs" / "run" / "final"
    gds = final_dir / "gds" / f"{top}.gds"
    if not gds.is_file():
        raise CheckError(f"no hardened GDS at {gds} - has the harden gate run?")

    pdk_root = _pdk_root()
    work_dir = ws / "log" / "drc_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    magic_count = run_magic_drc(gds, top, work_dir)
    klayout_count = run_klayout_drc(gds, top, pdk_root, work_dir)

    violations = []
    if magic_count:
        violations.append(checklib.violation(
            "drc", "error", None, top, "magic_drc_violation", [],
            f"magic DRC: {magic_count} violation(s)", "magic",
            count=magic_count))
    if klayout_count:
        violations.append(checklib.violation(
            "drc", "error", None, top, "klayout_drc_violation", [],
            f"klayout gf180mcu.drc: {klayout_count} violation(s)", "klayout",
            count=klayout_count))

    payload = checklib.report(SCRIPT, ws / "harden", violations, top=top,
                              magic_count=magic_count, klayout_count=klayout_count)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
