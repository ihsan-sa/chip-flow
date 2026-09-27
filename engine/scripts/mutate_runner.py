#!/usr/bin/env python
"""mutate_runner.py - the mcy `[test sim] run` driver for the mutate gate
(check_mutate.py, docs/design.md 1.5's mutate row, "### M2.").

Invoked by mcy itself (foss/tools/yosys/bin/mcy's `run_task()`) with cwd set
to one `tasks/<uuid>/` scratch directory under check_mutate.py's mcy project
directory, and PRJDIR/TASKDIR exported (mcy's own contract - see that
script's run_task()). Reads `input.txt` (one `<idx> mutate -mode ...` line
per queued mutation - mcy's own format, `create_mutated.sh` in
share/mcy/scripts is the upstream example this mirrors), yosys-applies each
mutation to the frozen `database/design.il` mcy already built, then builds
and runs the workspace's VISIBLE `tb/test_*.py` cocotb suite against the
resulting mutated netlist. Writes `output.txt` with one `<idx> PASS|FAIL`
line per input line: PASS = every visible test still passed (the mutant
SURVIVED, undetected), FAIL = at least one visible test failed, or the
mutated netlist would not even build (the mutant was KILLED). Before it
simulates, each netlist's edge-triggered flops are regrouped into one block
per sensitivity list, in RTL assignment order (regroup_flops() says why).

When mcy's own `-mode none` baseline fails to build (yosys or Icarus), the
tool's error goes to PRJDIR/baseline_build_error.txt, so check_mutate.py
can say the design did not build rather than that the tests failed.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(ENGINE / "lib"))
import cocotblib  # noqa: E402

BASELINE_BUILD_ERROR = "baseline_build_error.txt"  # check_mutate.py reads it


def tail(path: Path, n: int = 20) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"(no log at {path.name})"
    return "\n".join(lines[-n:])


# write_verilog gives every flop (every BIT, after techmap) its own
# `always @(posedge clk)` block, and Icarus runs blocks woken by the same
# edge in an order of its own. The RTL put `count <= ...; valid <= 1;` in
# ONE process, where IEEE 1364 applies the nonblocking updates in the order
# the statements ran - so a tb that reads count on RisingEdge(valid) sees
# the new count on the RTL and the old one on the netlist, and the
# unmutated design "fails" (sensor_counted_digital: count read 0, 3/4
# tests). regroup_flops() puts every edge-triggered block with the same
# sensitivity list back into one block, ordered by where each flop is first
# assigned in the RTL its src attribute names, so the netlist's update order
# is the RTL's again. It changes no logic: only the order of updates the
# standard leaves undefined across blocks.
_ALWAYS = re.compile(r"^  always @\((.*\b(?:pos|neg)edge\b.*)\)( begin)?$")
_SRC = re.compile(r'^\s*\(\* src = "([^"|]+?):(\d+)\.\d+-(\d+)\.\d+[|"]')
_LHS = re.compile(r"(\\\S+|[A-Za-z_][\w$]*)\s*(?:\[[^\]]*\]\s*)?<=")
_BLOCKING = re.compile(r"(?<![<>=!])=(?!=)")


def _rtl_position(src: str | None, lhs: str, cache: dict) -> tuple | None:
    """(file, line, col) of the first `lhs <=` inside the src range, or None."""
    m = _SRC.match(src or "")
    if not m:
        return None
    path, first, last = m.group(1), int(m.group(2)), int(m.group(3))
    if path not in cache:
        try:
            cache[path] = Path(path).read_text(
                encoding="utf-8", errors="replace").splitlines()
        except OSError:
            cache[path] = None
    lines = cache[path]
    if not lines:
        return None
    name = lhs[1:] if lhs.startswith("\\") else lhs
    pat = re.compile(r"(?<![\w$.])" + re.escape(name)
                     + r"\s*(?:\[[^\]]*\]\s*)?<=")
    for n in range(first - 1, min(last, len(lines))):
        hit = pat.search(lines[n])
        if hit:
            return (path, n + 1, hit.start())
    return None


def regroup_flops(text: str) -> str:
    lines = text.split("\n")
    out: list = []          # str lines, or a group key standing for a block
    groups: dict[str, list] = {}
    cache: dict = {}
    i = 0
    while i < len(lines):
        m = _ALWAYS.match(lines[i])
        j = i + 1
        while j < len(lines) and lines[j].startswith("    "):
            j += 1
        body = lines[i + 1:j]
        if m and m.group(2):            # `begin`: take its `end` too
            if j < len(lines) and lines[j] == "  end":
                body, j = body + ["  end"], j + 1
            else:
                m = None
        if (not m or not body or any(_BLOCKING.search(b) for b in body)
                or not _LHS.search(body[0])):
            out.append(lines[i])
            i += 1
            continue
        src = None
        if out and isinstance(out[-1], str) and out[-1].startswith("  (* src"):
            src = out.pop()
        stmt = body[:-1] if m.group(2) else body  # drop a block's `end`
        key = m.group(1)
        pos = _rtl_position(src, _LHS.search(body[0]).group(1), cache)
        if key not in groups:
            groups[key] = []
            out.append(("group", key))
        groups[key].append((pos is None, pos or (), len(groups[key]), stmt))
        i = j
    result: list[str] = []
    for item in out:
        if isinstance(item, str):
            result.append(item)
            continue
        blocks = sorted(groups[item[1]], key=lambda b: b[:3])
        result.append(f"  always @({item[1]}) begin")
        for *_, stmt in blocks:
            result.extend(stmt)
        result.append("  end")
    return "\n".join(result)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tb", required=True, help="the visible tb/ directory")
    ap.add_argument("--top", required=True, help="top module name")
    args = ap.parse_args(argv)

    taskdir = Path.cwd()
    prjdir = Path(os.environ["PRJDIR"])
    design_il = prjdir / "database" / "design.il"
    tb_dir = Path(args.tb)
    modules = cocotblib.test_modules(tb_dir)

    lines = (taskdir / "input.txt").read_text(encoding="utf-8").splitlines()
    results: list[tuple[str, str]] = []
    for line in lines:
        if not line.strip():
            continue
        idx, mutate_cmd = line.split(None, 1)
        baseline = "-mode none" in mutate_cmd
        mutated_v = taskdir / f"mutated_{idx}.v"
        ys = taskdir / f"mutate_{idx}.ys"
        # techmap before write_verilog: a design with an un-lowered
        # $procmux cell (proc's own output, needed as-is for `mutate -list`
        # to pick meaningful cells/ports) writes out as a bare
        # "$procmux$N(...)" call Icarus refuses ("System function ... not
        # defined") - harmless on a small design like counter8's single
        # mux, fatal on uart_tx's nested case statement. techmap lowers it
        # to primitives write_verilog can actually emit as legal Verilog,
        # with no effect on which cell/port the mutation itself targeted
        # (that was already fixed by the `mutate` line above).
        ys.write_text(f"read_rtlil {design_il}\n{mutate_cmd}\ntechmap\n"
                      f"write_verilog -norename {mutated_v}\n",
                      encoding="utf-8")
        yosys_rc = subprocess.run(
            ["yosys", "-ql", str(taskdir / f"mutate_{idx}.log"), str(ys)],
            cwd=str(taskdir)).returncode
        if yosys_rc != 0 or not mutated_v.is_file():
            if baseline:
                (prjdir / BASELINE_BUILD_ERROR).write_text(
                    "yosys could not write it out:\n"
                    + tail(taskdir / f"mutate_{idx}.log"), encoding="utf-8")
            # the mutation itself did not even produce a legal netlist -
            # that is caught, not survived.
            results.append((idx, "FAIL"))
            continue
        mutated_v.write_text(regroup_flops(
            mutated_v.read_text(encoding="utf-8")), encoding="utf-8")

        build_dir = taskdir / f"sim_build_{idx}"
        results_xml = taskdir / f"results_{idx}.xml"
        try:
            xml_path = cocotblib.run_cocotb(build_dir, tb_dir, [mutated_v],
                                            args.top, modules, results_xml)
            parsed = cocotblib.parse_results_xml(xml_path)
            killed = (not parsed) or any(not r["passed"]
                                         for r in parsed.values())
            results.append((idx, "FAIL" if killed else "PASS"))
        except Exception:  # noqa: BLE001 - a build/run crash means "caught"
            # no sim.vvp: iverilog itself refused the netlist
            if baseline and not (build_dir / "sim.vvp").is_file():
                (prjdir / BASELINE_BUILD_ERROR).write_text(
                    "Icarus could not build it:\n"
                    + tail(build_dir / "sim.log"), encoding="utf-8")
            results.append((idx, "FAIL"))

    with open(taskdir / "output.txt", "w", encoding="utf-8") as f:
        for idx, res in results:
            print(f"{idx} {res}", file=f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
