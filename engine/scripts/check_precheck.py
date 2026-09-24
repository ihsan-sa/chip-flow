#!/usr/bin/env python
"""check_precheck.py - the precheck gate (docs/design.md 1.5, "### M4.").

    check_precheck.py --workspace DIR [--out FILE]

Runs the vendored, unmodified tt-support-tools `precheck.py`
(engine/reference/tt/VENDORED.md has the pinned commit and the one
integration shim) against the hardened GDS - the signoff check the shuttle
itself runs before accepting a submission, and the one this milestone adds
that nothing else in the pipeline does (docs/design.md, "### M4."'s "why").

precheck.py hardcodes its own report path and expects `<gds-stem>.lef` and
`<gds-stem>.v` next to (or one directory above) the GDS, plus an `info.yaml`
somewhere above it - none of which matches `harden/runs/run/final/`'s own
layout (lef/, gds/, pnl/ as separate directories). Rather than either
mutate that run directory or patch the vendored script, this gate copies
the whole `precheck/` tool plus exactly the four files it needs
(GDS, LEF, the POWERED netlist as `<name>.v` - the power-pin check reads
VPWR/VGND ports precheck.py expects there - and info.yaml) into a scratch
directory under `log/precheck_run/`, fresh every run, and runs it there.

Passes when precheck.py exits 0 (gates.yaml's `precheck` row: "passes").
Fault this gate must catch: "a wrong top module name in info.yaml" -
`stage_precheck_run`'s `declared_top` docstring has the mechanism
(corpus/vde/*/faults/plant_precheck.py sets CHIP_FLOW_PRECHECK_TOP_OVERRIDE
so the staged files and info.yaml agree with each other but not with the
GDS's own internal cell name, landing on precheck.py's "KLayout Checks"
step's own mismatch check - a clean `<testcase><error>`, never a crash
before results.xml exists).

Failure classification: no hardened GDS/LEF/netlist/info.yaml yet ->
CheckError. `eda python3` crashes before writing `reports/results.xml` (a
missing python dependency precheck's own pydeps cache did not resolve, a
timeout) -> CheckError. `results.xml` exists and records >=1 failed
testcase (including the info.yaml top-module mismatch above) ->
a `violations` finding, exit 1.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import speclib  # noqa: E402
import ttlib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_precheck"
EDA_BIN = REPO / "bin" / "eda"
TIMEOUT_S = 300.0


def _pdk_root() -> Path:
    proc = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError(f"eda --print-toolchain-root failed: "
                         f"{proc.stderr.strip()}")
    return Path(proc.stdout.strip()) / "foss" / "pdks"


def stage_precheck_run(ws: Path, spec: dict, final_dir: Path, top: str,
                       declared_top: str | None = None) -> Path:
    """log/precheck_run/, rebuilt fresh: a copy of engine/reference/tt/
    tt-support-tools/precheck/ (so its own hardcoded reports/ path lands
    somewhere workspace-scoped, never inside the vendored tree - two
    designs' gates never race on the same reports/ directory either) plus
    a design/ subdirectory laid out the way precheck.py's own path-walking
    expects: <top>.gds, <top>.lef, <top>.v (the POWERED netlist - the
    power-pin check wants VPWR/VGND ports) and info.yaml one level up.

    `declared_top` (default: `top`) is what gets written into info.yaml's
    `top_module` AND what the staged files are named after - never what is
    read out of `final_dir` (always the real `top`). The two are the same
    in every real run; corpus/vde/*/faults/plant_precheck.py (the fault
    gates.yaml names for this gate, "a wrong top module name in info.yaml")
    is the one caller that gives a different string, via
    CHIP_FLOW_PRECHECK_TOP_OVERRIDE below - the staged GDS is still a
    byte-for-byte copy of the REAL one, so its own top-level cell NAME
    inside stays the real `top`; only the file's own name and info.yaml's
    claim about it change, which is exactly what precheck.py's `assert
    top_module == basename(gds_stem)` (satisfied - both sides read
    `declared_top`) followed by its "KLayout Checks" step's
    `top_cell.name != expected_name` (not satisfied - the cell inside is
    still really `top`) is designed to catch."""
    declared_top = declared_top or top
    run_dir = ws / "log" / "precheck_run"
    shutil.rmtree(run_dir, ignore_errors=True)
    shutil.copytree(ttlib.PRECHECK_DIR, run_dir / "precheck")
    # precheck.py writes every check's own report (drc_*.xml, results.xml,
    # results.md) under REPORTS_PATH = dirname(__file__)/reports - a
    # directory the vendored tree does not carry (nothing writes there
    # until a real run), so the copy above never creates it either.
    (run_dir / "precheck" / "reports").mkdir(parents=True, exist_ok=True)
    # precheck.py's own main() also reads its DEF template from a path
    # relative to ITS OWN cwd, "../tech/<tech>/def/..." - the real
    # tt-support-tools repo has precheck/ and tech/ as siblings, so the
    # staged run directory has to reproduce that same sibling layout, not
    # just precheck/ on its own.
    shutil.copytree(ttlib.SUPPORT_DIR / "tech", run_dir / "tech")
    design_dir = run_dir / "design"
    design_dir.mkdir(parents=True)

    gds = final_dir / "gds" / f"{top}.gds"
    lef = final_dir / "lef" / f"{top}.lef"
    pnl = final_dir / "pnl" / f"{top}.pnl.v"
    for label, p in (("GDS", gds), ("LEF", lef), ("powered netlist", pnl)):
        if not p.is_file():
            raise CheckError(f"no {label} at {p} - has the harden gate run?")
    shutil.copy2(gds, design_dir / f"{declared_top}.gds")
    shutil.copy2(lef, design_dir / f"{declared_top}.lef")
    shutil.copy2(pnl, design_dir / f"{declared_top}.v")
    spec_for_info = {**spec, "top": declared_top.removeprefix("tt_um_")} \
        if declared_top != top else spec
    ttlib.write_info_yaml(spec_for_info, run_dir / "info.yaml")
    return run_dir


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = ttlib.wrapper_name(spec)
    final_dir = ws / "harden" / "runs" / "run" / "final"
    if not final_dir.is_dir():
        raise CheckError(f"no {final_dir} - the harden gate has not run yet")

    import os
    # test/fault hook only - see stage_precheck_run's docstring. Never set
    # by any real gate.py/jobs.py invocation.
    declared_top = os.environ.get("CHIP_FLOW_PRECHECK_TOP_OVERRIDE") or None
    run_dir = stage_precheck_run(ws, spec, final_dir, top, declared_top)
    staged_top = declared_top or top
    precheck_dir = run_dir / "precheck"
    precheck_py = precheck_dir / "precheck.py"

    pdk_root = _pdk_root()
    pydeps = ttlib.ensure_precheck_deps(EDA_BIN)
    shim_dir = ttlib.yowasp_yosys_shim_dir(EDA_BIN, pydeps)

    env = os.environ.copy()
    env["PDK_ROOT"] = str(pdk_root)
    env["PDK"] = ttlib.PDK_NAME
    env["PYTHONPATH"] = str(pydeps) + (
        (":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    env["PATH"] = str(shim_dir) + ":" + env.get("PATH", "")

    gds_path = run_dir / "design" / f"{staged_top}.gds"
    try:
        proc = subprocess.run(
            [str(EDA_BIN), "python3", str(precheck_py), "--gds", str(gds_path),
             "--tech", ttlib.PDK_NAME],
            cwd=str(precheck_dir), stdin=subprocess.DEVNULL, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"precheck.py timed out after {TIMEOUT_S:g}s: {exc}") from exc

    results_xml = precheck_dir / "reports" / "results.xml"
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if not results_xml.is_file():
        raise CheckError(
            f"precheck.py wrote no {results_xml} (exit {proc.returncode}): "
            f"{output[-2000:]}")

    tree = ET.parse(results_xml)
    violations = []
    for tc in tree.iter("testcase"):
        name = tc.attrib.get("name", "?")
        err = tc.find("error")
        if err is not None:
            violations.append(checklib.violation(
                "precheck", "error", None, name, "precheck_failed", [],
                f"{name}: {err.attrib.get('message') or (err.text or '').strip()}",
                "tt-precheck"))

    payload = checklib.report(SCRIPT, ws / "harden", violations, top=top,
                              checks_run=sum(1 for _ in tree.iter("testcase")))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
