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
mutated netlist would not even build (the mutant was KILLED).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(ENGINE / "lib"))
import cocotblib  # noqa: E402


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
        mutated_v = taskdir / f"mutated_{idx}.v"
        ys = taskdir / f"mutate_{idx}.ys"
        ys.write_text(f"read_rtlil {design_il}\n{mutate_cmd}\n"
                      f"write_verilog -norename {mutated_v}\n",
                      encoding="utf-8")
        yosys_rc = subprocess.run(
            ["yosys", "-ql", str(taskdir / f"mutate_{idx}.log"), str(ys)],
            cwd=str(taskdir)).returncode
        if yosys_rc != 0 or not mutated_v.is_file():
            # the mutation itself did not even produce a legal netlist -
            # that is caught, not survived.
            results.append((idx, "FAIL"))
            continue

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
            results.append((idx, "FAIL"))

    with open(taskdir / "output.txt", "w", encoding="utf-8") as f:
        for idx, res in results:
            print(f"{idx} {res}", file=f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
