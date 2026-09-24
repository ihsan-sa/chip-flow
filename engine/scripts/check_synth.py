#!/usr/bin/env python
"""check_synth.py - the synth gate (docs/design.md 1.5, "### M3.").

    check_synth.py --workspace DIR [--out FILE]

Runs yosys (through `bin/eda`) over rtl/*.v: generic `synth -top`, then
`dfflibmap`/`abc` against the gf180mcu_fd_sc_mcu9t5v0 typical-corner liberty
(the same recipe tests/check.sh's own yosys-synth-gf180mcu smoke uses),
writing the mapped netlist to `synth/<top>.v`.

Pass criteria (gates.yaml `synth` row): no latches, unmapped cells or
combinational loops; area recorded.
  combinational loop  yosys's own `synth`/`opt` passes ALREADY detect and
                       print "Warning: found logic loop in module ..." as
                       part of the normal flow (proved empirically - no
                       extra `check`/`torder` pass is needed, and ABC even
                       goes on to break the loop and finish the run anyway,
                       so a missing check here would let a real combinational
                       loop through silently). This is gates.yaml's own
                       named fault for this gate.
  unmapped cell        after `abc -liberty`, every surviving cell should be
                       a `gf180mcu_fd_sc_mcu9t5v0__*` liberty cell; anything
                       still named `$...` (yosys's internal generic cell
                       types - never a legal Verilog/liberty identifier
                       prefix) never got mapped.
  latch                a gf180mcu latch cell survives all the way to the
                       final netlist (`..._lat*_..` - checked directly
                       against the liberty's own cell names, e.g.
                       `gf180mcu_fd_sc_mcu9t5v0__latq_1`; `..._dly*_..`
                       delay cells are NOT latches and must not match).
                       Defense in depth: `lint` (verilator) already catches
                       an inferred latch pre-synthesis; this is the same
                       check one stage later, in case something reaches
                       synth without going through lint first.
`check -assert` was tried and rejected here: on this exact liberty/ABC
recipe it reports "Wire counter8.count[N] is used but has no driver" on
every clean, correctly-synthesized design (verified against the final
`write_verilog` output, which DOES drive every such bit from a DFF's Q pin -
`check`'s own port-driver bookkeeping just does not survive ABC's blif
round-trip for an output driven directly by a mapped flop). Using it
would fail every clean design in this flow, so this gate parses yosys's own
free-text warnings and `stat`'s cell histogram directly instead.
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
from checklib import CheckError  # noqa: E402

SCRIPT = "check_synth"
EDA_BIN = REPO / "bin" / "eda"
TIMEOUT_S = 120.0
LIBERTY_REL = ("foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu9t5v0/lib/"
              "gf180mcu_fd_sc_mcu9t5v0__tt_025C_5v00.lib")

LOOP_RE = re.compile(r"found logic loop in module (\S+?):")
# `stat -liberty <lib>` prints a THREE-column breakdown (count, per-cell
# area, name - proved empirically; plain `stat` with no liberty prints only
# two) under a summary line of its own shape ("<N> <total-area> cells");
# CELLS_HEADER_RE finds that summary line so CELL_ROW_RE only ever reads
# the rows directly under it, never anything above (an unrelated earlier
# "<N> <name>" pair - "23 wires", "3 ports" - would otherwise false-match).
CELLS_HEADER_RE = re.compile(r"^\s*\d+\s+\S+\s+cells\s*$")
CELL_ROW_RE = re.compile(r"^\s*(\d+)\s+[0-9.eE+-]+\s+(\S+)\s*$")
AREA_RE = re.compile(r"Chip area for module '\\?(\S+?)':\s*([0-9.]+)")
LATCH_RE = re.compile(r"__lat[a-z]*_\d+$")


def collect_sources(ws: Path) -> list[Path]:
    rtl_dir = ws / "rtl"
    files = sorted(rtl_dir.glob("*.v")) + sorted(rtl_dir.glob("*.sv"))
    if not files:
        raise CheckError(f"no .v/.sv files under {rtl_dir}")
    return files


def toolchain_root(timeout: float = 30.0) -> Path:
    proc = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError("could not resolve the eda toolchain root: "
                         f"{(proc.stderr or proc.stdout).strip()}")
    return Path(proc.stdout.strip())


def run_yosys(ws: Path, top: str, rtl_files: list[Path], liberty: Path,
             out_v: Path) -> str:
    rel = [str(f.relative_to(ws)) for f in rtl_files]
    script = ws / "log" / "synth.ys"
    script.parent.mkdir(parents=True, exist_ok=True)
    out_v.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"""\
{chr(10).join(f'read_verilog {f}' for f in rel)}
hierarchy -top {top}
synth -top {top}
dfflibmap -liberty {liberty}
abc -liberty {liberty}
clean
stat -liberty {liberty}
write_verilog {out_v.relative_to(ws)}
""", encoding="utf-8")
    try:
        proc = subprocess.run(
            [str(EDA_BIN), "yosys", "-s", str(script.relative_to(ws))],
            cwd=str(ws), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"yosys synth timed out after {TIMEOUT_S:g}s: "
                         f"{exc}") from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise CheckError(f"yosys exited {proc.returncode}: "
                         f"{output[-2000:]}")
    if "Chip area for module" not in output:
        raise CheckError("yosys never reached `stat` (no area line in its "
                         f"output) - the run did not complete: "
                         f"{output[-2000:]}")
    return output


def cell_histogram(output: str) -> dict[str, int]:
    """The FINAL `stat -liberty` cell breakdown - `stat` runs more than
    once during `synth`'s own internal passes (its generic-cell breakdown
    before dfflibmap/abc ever run is not what this gate wants), so only the
    rows under the LAST "<N> <area> cells" summary line (the post-abc,
    post-clean one this script's own script asked for) are counted."""
    lines = output.splitlines()
    header_i = None
    for i, line in enumerate(lines):
        if CELLS_HEADER_RE.match(line):
            header_i = i
    if header_i is None:
        raise CheckError("no '<N> <area> cells' summary line in yosys's "
                         "`stat -liberty` output")
    cells: dict[str, int] = {}
    for line in lines[header_i + 1:]:
        if not line.strip():
            break
        m = CELL_ROW_RE.match(line)
        if m:
            cells[m.group(2)] = int(m.group(1))
    return cells


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--liberty", help="override the liberty file "
                    "(default: the gf180mcu typical-corner std cell lib)")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = spec.get("top")
    if not isinstance(top, str) or not top.strip():
        raise CheckError("spec.yaml has no non-empty 'top' - run spec_lint "
                         "first")
    rtl_files = collect_sources(ws)
    liberty = (Path(args.liberty) if args.liberty
              else toolchain_root() / LIBERTY_REL)
    if not liberty.is_file():
        raise CheckError(f"liberty file not found: {liberty}")

    out_v = ws / "synth" / f"{top}.v"
    output = run_yosys(ws, top, rtl_files, liberty, out_v)

    violations = []
    loop_mods = sorted(set(LOOP_RE.findall(output)))
    for mod in loop_mods:
        violations.append(checklib.violation(
            "synth", "error", None, mod, "combinational_loop", [],
            f"yosys found a combinational logic loop in module {mod}",
            "yosys"))

    cells = cell_histogram(output)
    unmapped = sorted(name for name in cells if name.startswith("$"))
    for name in unmapped:
        violations.append(checklib.violation(
            "synth", "error", None, None, "unmapped_cell", [],
            f"{cells[name]} instance(s) of {name} were never mapped to the "
            "gf180mcu liberty", "yosys"))
    latches = sorted(name for name in cells if LATCH_RE.search(name))
    for name in latches:
        violations.append(checklib.violation(
            "synth", "error", None, None, "latch", [],
            f"{cells[name]} instance(s) of latch cell {name} in the "
            "synthesized netlist", "yosys"))

    m = AREA_RE.search(output)
    area = float(m.group(2)) if m else None
    if area is None and not violations:
        raise CheckError("no 'Chip area for module' figure in yosys's "
                         "output - area could not be recorded")

    payload = checklib.report(
        SCRIPT, ws / "rtl", violations, top=top,
        cells={k: v for k, v in sorted(cells.items())}, area=area,
        netlist=str(out_v.relative_to(ws)))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
