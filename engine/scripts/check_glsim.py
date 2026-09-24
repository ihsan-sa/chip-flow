#!/usr/bin/env python
"""check_glsim.py - the glsim gate (docs/design.md 1.5, "### M4.").

    check_glsim.py --workspace DIR [--out FILE]

Runs the SAME tb/*.py cocotb suite `sim` uses (docs/design.md section 2:
"a design can be right in RTL and wrong after synthesis") against the
hardened gate-level netlist (`harden/runs/run/final/nl/*.nl.v`), twice:

  functional - the PDK's own cell models compiled with `-DFUNCTIONAL` (their
  own convention, `` `ifndef FUNCTIONAL `` around every `specify` block -
  engine/reference/tt/VENDORED.md), zero-delay, proves the netlist is
  logically equivalent to the RTL through a real gate-level structural sim.

  sdf - the same models WITHOUT that define (specify blocks live), with the
  hardened design's own nominal-corner SDF back-annotated
  (`$sdf_annotate`, engine/lib/ttlib.py's `generate_glsim_harness`) -
  proves the netlist is not just logically right but free of the races an
  X-propagating read-before-write only shows up under real timing (the
  fault this gate must catch, gates.yaml's `glsim` row).

The harness (`ttlib.generate_glsim_harness`) is the tt_pins map read in
reverse: a module named exactly spec['top'], the original RTL's own port
list, wrapping the hardened `tt_um_<top>` netlist - so tb/*.py drives and
observes it exactly as it drives the pre-synthesis RTL, unmodified.

Passes when every test in tb/ passes BOTH ways (gates.yaml: "every test
passes both ways"). Failure classification (the same three-way split every
M4 gate uses): no hardened netlist yet, or no PDK cell verilog/SDF for a
pass -> CheckError. Icarus/cocotb producing no results.xml at all (a build
failure, a crash before a single test ran) -> CheckError - a pass that
never ran is a refusal, never silently 2-for-2. A completed run with a
failing or skipped test, in either pass -> a `violations` finding, exit 1.
"""
from __future__ import annotations

import argparse
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
import ttlib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_glsim"
EDA_BIN = REPO / "bin" / "eda"
SDF_CORNER = "nom_tt_025C_3v30"


def _pdk_root() -> Path:
    proc = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError(f"eda --print-toolchain-root failed: "
                         f"{proc.stderr.strip()}")
    return Path(proc.stdout.strip()) / "foss" / "pdks"


def _cell_sources(pdk_root: Path) -> list[Path]:
    verilog_dir = (pdk_root / ttlib.PDK_NAME / "libs.ref" /
                   "gf180mcu_fd_sc_mcu7t5v0" / "verilog")
    primitives = verilog_dir / "primitives.v"
    cells = verilog_dir / "gf180mcu_fd_sc_mcu7t5v0.v"
    for p in (primitives, cells):
        if not p.is_file():
            raise CheckError(f"no PDK gate-level verilog at {p}")
    return [primitives, cells]


def run_pass(name: str, ws: Path, spec: dict, final_dir: Path,
            cell_sources: list[Path], build_dir: Path,
            functional: bool, sdf: Path | None) -> dict:
    top = spec["top"]
    dut_module = ttlib.wrapper_name(spec)
    nl = final_dir / "nl" / f"{dut_module}.nl.v"
    if not nl.is_file():
        raise CheckError(f"no gate-level netlist at {nl} - has the harden "
                         "gate run?")
    harness = build_dir / f"{top}_glsim_{name}.v"
    try:
        harness.write_text(
            ttlib.generate_glsim_harness(spec, sdf_path=sdf), encoding="utf-8")
    except ttlib.TTError as exc:
        raise CheckError(str(exc)) from exc

    tb_dir = ws / "tb"
    modules = cocotblib.test_modules(tb_dir)
    if not modules:
        raise CheckError(f"no test_*.py modules under {tb_dir}")

    sources = [harness, nl, *cell_sources]
    defines_file = None
    build_args = []
    if functional:
        defines_file = build_dir / "functional_define.v"
        defines_file.write_text("`define FUNCTIONAL\n", encoding="utf-8")
        sources = [defines_file, *sources]

    from cocotb_tools.runner import get_runner
    runner = get_runner("icarus")
    log_file = str(build_dir / "sim.log")
    pass_build_dir = build_dir / f"build_{name}"
    results_xml = build_dir / f"results_{name}.xml"
    runner.build(sources=[str(s) for s in sources], hdl_toplevel=top,
                build_dir=str(pass_build_dir), waves=False,
                timescale=("1ns", "1ps"), log_file=log_file,
                build_args=build_args)
    try:
        runner.test(hdl_toplevel=top, test_module=modules, test_dir=str(tb_dir),
                   build_dir=str(pass_build_dir), results_xml=str(results_xml),
                   waves=False, log_file=log_file)
    except SystemExit:
        pass
    if not Path(results_xml).is_file():
        raise CheckError(f"{name} pass: cocotb produced no {results_xml} - "
                         "the build or the run never completed")
    results = cocotblib.parse_results_xml(Path(results_xml))
    if not results:
        raise CheckError(f"{name} pass: cocotb produced no test cases in "
                         f"{results_xml}")
    return results


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = spec.get("top")
    if not isinstance(top, str) or not top.strip():
        raise CheckError("spec.yaml has no non-empty 'top' - run spec_lint first")
    if not spec.get("tt_pins"):
        raise CheckError("spec.yaml has no 'tt_pins' mapping")

    final_dir = ws / "harden" / "runs" / "run" / "final"
    if not final_dir.is_dir():
        raise CheckError(f"no {final_dir} - the harden gate has not run yet")

    dut_module = ttlib.wrapper_name(spec)
    sdf = final_dir / "sdf" / SDF_CORNER / f"{dut_module}__{SDF_CORNER}.sdf"
    if not sdf.is_file():
        raise CheckError(f"no SDF at {sdf}")

    pdk_root = _pdk_root()
    cell_sources = _cell_sources(pdk_root)

    build_root = ws / "log" / "glsim_build"
    shutil.rmtree(build_root, ignore_errors=True)
    build_root.mkdir(parents=True, exist_ok=True)

    violations = []
    all_results = {}
    for name, functional, sdf_path in (
        ("functional", True, None),
        ("sdf", False, sdf),
    ):
        results = run_pass(name, ws, spec, final_dir, cell_sources, build_root,
                          functional, sdf_path)
        all_results[name] = {k: v["passed"] for k, v in results.items()}
        for tname, res in sorted(results.items()):
            if res["passed"]:
                continue
            kind = "test_skipped" if res.get("skipped") else "test_failed"
            violations.append(checklib.violation(
                "glsim", "error", None, tname, kind, [],
                f"[{name}] {tname}: "
                f"{res['message'] or 'see log/glsim_build/sim.log'}",
                "cocotb", glsim_pass=name))

    payload = checklib.report(SCRIPT, ws / "harden", violations, top=top,
                              results=all_results)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
