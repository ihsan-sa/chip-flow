#!/usr/bin/env python
"""check_synth.py - the synth gate (docs/design.md 1.5, "### M3.").

    check_synth.py --workspace DIR [--out FILE]

Runs yosys (through `bin/eda`) over rtl/*.v: `read_liberty -lib` of the
chosen standard-cell liberty (so the RTL may instantiate its cells directly),
generic `synth -flatten -top`, then `dfflibmap`/`abc` against that same
liberty, writing the mapped netlist to `synth/<top>.v` (and its JSON to
`synth/<top>.json`).

Why flattened: on a hierarchical netlist `stat` prints one section per
module, and the last one - the top's - lists the design's own submodules
(and a "submodules" summary row) as if they were cells, while the cells
inside those submodules are never seen at all. A latch or unmapped cell in
a submodule would pass and every submodule would fail as a cell missing
from the liberty. Flattened, the top's one section holds every leaf cell,
and the area is the whole design's. Flattening could only cost a cell the
design hand-instantiated, so spec.yaml's `must_keep` (instance or net
names, the same list the optimiser keeps - docs/design.md section 4) is
checked on the flattened netlist: a name it no longer has is a finding.
`flatten` leaves one `$scopeinfo` cell per former instance - names only,
no logic, no area - and they are deleted before `stat`, so they are never
read as unmapped cells.

Which liberty: spec.yaml's optional `std_cell: {library, corner}` (e.g.
`{library: gf180mcu_fd_sc_mcu7t5v0, corner: tt_025C_3v30}`). Without it, a
spec that names a Tiny Tapeout target (`tt_pins` or `tiles`) gets the TT GF
template's own synthesis liberty (the vendored tech.py's `LIB_SYNTH`,
gf180mcu_fd_sc_mcu7t5v0 tt_025C_3v30 - the library LibreLane hardens it
with), and any other spec gets gf180mcu_fd_sc_mcu9t5v0 tt_025C_5v00.
`--liberty` overrides all three.

Pass criteria (gates.yaml `synth` row): no latches, unmapped cells or
combinational loops, no `must_keep` name lost; area recorded.
  combinational loop  yosys's own `synth`/`opt` passes ALREADY detect and
                       print "Warning: found logic loop in module ..." as
                       part of the normal flow (proved empirically - no
                       extra `check`/`torder` pass is needed, and ABC even
                       goes on to break the loop and finish the run anyway,
                       so a missing check here would let a real combinational
                       loop through silently). This is gates.yaml's own
                       named fault for this gate.
  cell not in liberty  a cell the RTL instantiates by name that the chosen
                       liberty does not define: yosys's `hierarchy` refuses
                       the unknown module, or (when the RTL carries its own
                       blackbox stub of it) it survives into the netlist
                       under a name the liberty has no `cell(...)` for.
                       Either way it is a finding naming the liberty, never
                       a pass over a netlist the chosen library cannot build.
  unmapped cell        after `abc -liberty`, every surviving cell should be
                       a cell of the chosen liberty; anything
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
  must_keep removed    a spec.yaml `must_keep` name that neither a cell nor
                       a net of the flattened top carries (`<name>`, a
                       hierarchical `....<name>` or anything under
                       `<name>.`) - optimised or flattened away.
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
import json
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
LIBS_REF_REL = "foss/pdks/gf180mcuD/libs.ref"
STD_CELL_LIBRARIES = ("gf180mcu_fd_sc_mcu7t5v0", "gf180mcu_fd_sc_mcu9t5v0")
DEFAULT_STD_CELL = {"library": "gf180mcu_fd_sc_mcu9t5v0",
                    "corner": "tt_025C_5v00"}
CORNER_RE = re.compile(r"^(tt|ss|ff)_n?\d+C_\dv\d\d$")
LIB_SYNTH_RE = re.compile(r"libs\.ref/(gf180mcu_fd_sc_\w+?)/lib/"
                          r"\1__(\w+)\.lib$")
LIB_CELL_RE = re.compile(r"^\s*cell\s*\(\s*\"?([\w$]+)\"?\s*\)", re.M)
MISSING_MODULE_RE = re.compile(
    r"Module `\\?(\S+?)' referenced in module `\\?(\S+?)'")

LOOP_RE = re.compile(r"found logic loop in module (\S+?):")
# yosys's own `synth`/`opt` passes print this for a net that is read but
# never driven, same as LOOP_RE's own logic-loop warning - no extra `check`
# pass needed (and `check -assert` was rejected above for false-positiving
# on every clean design's own flop-driven outputs; this text, from the
# ORDINARY flow, does not - proved empirically, same discipline as LOOP_RE).
NO_DRIVER_RE = re.compile(r"Wire (\S+?) is used but has no driver")
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


def tt_std_cell() -> dict:
    """The TT GF template's own synthesis liberty, read from the vendored
    tech.py's `LIB_SYNTH` rather than typed a second time here."""
    import ttlib
    value = ttlib.gf180_tech().librelane_config.get("LIB_SYNTH", "")
    m = LIB_SYNTH_RE.search(value)
    if not m:
        raise CheckError(f"unexpected LIB_SYNTH shape in the vendored "
                         f"tech.py: {value!r}")
    return {"library": m.group(1), "corner": m.group(2)}


def choose_std_cell(spec: dict) -> dict:
    """{library, corner, source} for this spec: its own `std_cell`, else
    the TT template's when it names a TT target, else DEFAULT_STD_CELL."""
    want = spec.get("std_cell")
    if want is None:
        if spec.get("tt_pins") or spec.get("tiles"):
            return {**tt_std_cell(), "source": "tt_template"}
        return {**DEFAULT_STD_CELL, "source": "default"}
    if not isinstance(want, dict) or set(want) - {"library", "corner"}:
        raise CheckError("spec.yaml 'std_cell' must be a mapping with "
                         "'library' and optional 'corner' only")
    lib = want.get("library")
    if lib not in STD_CELL_LIBRARIES:
        raise CheckError(f"spec.yaml std_cell.library {lib!r} is not one of "
                         f"{', '.join(STD_CELL_LIBRARIES)}")
    corner = want.get("corner") or ("tt_025C_3v30"
                                    if lib == "gf180mcu_fd_sc_mcu7t5v0"
                                    else DEFAULT_STD_CELL["corner"])
    if not isinstance(corner, str) or not CORNER_RE.match(corner):
        raise CheckError(f"spec.yaml std_cell.corner {corner!r} is not a "
                         "liberty corner name like tt_025C_3v30")
    return {"library": lib, "corner": corner, "source": "spec"}


def liberty_path(root: Path, std_cell: dict) -> Path:
    lib = std_cell["library"]
    return (root / LIBS_REF_REL / lib / "lib"
            / f"{lib}__{std_cell['corner']}.lib")


def liberty_cells(liberty: Path) -> set[str]:
    return set(LIB_CELL_RE.findall(
        liberty.read_text(encoding="utf-8", errors="replace")))


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
             out_v: Path) -> tuple[str, list[tuple[str, str]]]:
    """(yosys output, missing) - `missing` lists (cell, module) pairs
    `hierarchy` refused because the liberty does not define that cell;
    when it is non-empty yosys stopped there and the output is partial."""
    rel = [str(f.relative_to(ws)) for f in rtl_files]
    script = ws / "log" / "synth.ys"
    script.parent.mkdir(parents=True, exist_ok=True)
    out_v.parent.mkdir(parents=True, exist_ok=True)
    out_v.with_suffix(".json").unlink(missing_ok=True)
    script.write_text(f"""\
read_liberty -lib {liberty}
{chr(10).join(f'read_verilog {f}' for f in rel)}
hierarchy -top {top}
synth -flatten -top {top}
dfflibmap -liberty {liberty}
abc -liberty {liberty}
delete t:$scopeinfo
clean
stat -liberty {liberty}
write_verilog {out_v.relative_to(ws)}
write_json {out_v.with_suffix('.json').relative_to(ws)}
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
        missing = sorted(set(MISSING_MODULE_RE.findall(output)))
        if missing:
            return output, missing
        raise CheckError(f"yosys exited {proc.returncode}: "
                         f"{output[-2000:]}")
    if "Chip area for module" not in output:
        raise CheckError("yosys never reached `stat` (no area line in its "
                         f"output) - the run did not complete: "
                         f"{output[-2000:]}")
    return output, []


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


def must_keep_missing(netlist_json: dict, top: str, names: list[str]) -> list[str]:
    """section 4: "`must_keep` cells are checked after synth". A name is
    kept when a cell (or a net, for a signal) of the flattened netlist is
    that name, ends in `.<name>`, or sits under instance `<name>.`."""
    mod = (netlist_json.get("modules") or {}).get(top) or {}
    have = set((mod.get("cells") or {}).keys()) | set(
        (mod.get("netnames") or {}).keys())
    have = {h.lstrip("\\") for h in have}
    missing = []
    for n in names:
        if not any(h == n or h.endswith("." + n) or h.startswith(n + ".")
                   for h in have):
            missing.append(n)
    return missing


def spec_must_keep(spec: dict) -> list[str]:
    keep = spec.get("must_keep") or []
    if not isinstance(keep, list) or not all(
            isinstance(n, str) and n.strip() for n in keep):
        raise CheckError("spec.yaml 'must_keep' must be a list of instance "
                         "or net names")
    return keep


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--liberty", help="override the liberty file "
                    "(default: spec.yaml's std_cell, else the TT template's "
                    "for a TT target, else mcu9t5v0 tt_025C_5v00)")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = spec.get("top")
    if not isinstance(top, str) or not top.strip():
        raise CheckError("spec.yaml has no non-empty 'top' - run spec_lint "
                         "first")
    keep = spec_must_keep(spec)
    rtl_files = collect_sources(ws)
    if args.liberty:
        std_cell = {"library": None, "corner": None, "source": "--liberty"}
        liberty = Path(args.liberty)
    else:
        std_cell = choose_std_cell(spec)
        liberty = liberty_path(toolchain_root(), std_cell)
    if not liberty.is_file():
        raise CheckError(f"liberty file not found: {liberty}")
    std_cell["liberty"] = liberty.name

    out_v = ws / "synth" / f"{top}.v"
    output, missing = run_yosys(ws, top, rtl_files, liberty, out_v)

    violations = []
    for cell, mod in missing:
        violations.append(checklib.violation(
            "synth", "error", None, mod, "cell_not_in_liberty", [],
            f"module {mod} instantiates {cell}, which {liberty.name} does "
            "not define - pick the library that has it (spec.yaml "
            "std_cell) or use a cell this one has", "yosys"))
    if missing:
        payload = checklib.report(SCRIPT, ws / "rtl", violations, top=top,
                                  std_cell=std_cell, cells={}, area=None,
                                  netlist=None)
        return payload, args.out

    loop_mods = sorted(set(LOOP_RE.findall(output)))
    for mod in loop_mods:
        violations.append(checklib.violation(
            "synth", "error", None, mod, "combinational_loop", [],
            f"yosys found a combinational logic loop in module {mod}",
            "yosys"))

    undriven = sorted(set(NO_DRIVER_RE.findall(output)))
    for wire in undriven:
        violations.append(checklib.violation(
            "synth", "error", None, None, "no_driver", [],
            f"yosys found {wire} used but never driven", "yosys"))

    cells = cell_histogram(output)
    if not cells:
        raise CheckError("yosys's synthesized netlist has no cells at all "
                         "- an empty netlist is a refusal, never a pass")
    unmapped = sorted(name for name in cells if name.startswith("$"))
    for name in unmapped:
        violations.append(checklib.violation(
            "synth", "error", None, None, "unmapped_cell", [],
            f"{cells[name]} instance(s) of {name} were never mapped to "
            f"{liberty.name}", "yosys"))
    known = liberty_cells(liberty)
    for name in sorted(n for n in cells
                       if not n.startswith("$") and n not in known):
        violations.append(checklib.violation(
            "synth", "error", None, None, "cell_not_in_liberty", [],
            f"{cells[name]} instance(s) of {name} survive in the netlist but "
            f"{liberty.name} does not define that cell (a blackbox stub in "
            "rtl/ hides it from yosys) - pick the library that has it "
            "(spec.yaml std_cell) or use a cell this one has", "yosys"))
    latches = sorted(name for name in cells if LATCH_RE.search(name))
    for name in latches:
        violations.append(checklib.violation(
            "synth", "error", None, None, "latch", [],
            f"{cells[name]} instance(s) of latch cell {name} in the "
            "synthesized netlist", "yosys"))

    netlist_json = out_v.with_suffix(".json")
    try:
        nl = json.loads(netlist_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CheckError(f"yosys wrote no readable {netlist_json.name}, so "
                         f"must_keep could not be checked: {exc}") from exc
    for name in must_keep_missing(nl, top, keep):
        violations.append(checklib.violation(
            "synth", "error", None, top, "must_keep_removed", [],
            f"spec.yaml must_keep names {name}, but no cell or net of the "
            "flattened netlist carries it - mark the instance and its nets "
            "(* keep *) so synthesis cannot remove it", "yosys"))

    m = AREA_RE.search(output)
    area = float(m.group(2)) if m else None
    if area is None and not violations:
        raise CheckError("no 'Chip area for module' figure in yosys's "
                         "output - area could not be recorded")

    payload = checklib.report(
        SCRIPT, ws / "rtl", violations, top=top, std_cell=std_cell,
        cells={k: v for k, v in sorted(cells.items())}, area=area,
        netlist=str(out_v.relative_to(ws)))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
