#!/usr/bin/env python
"""check_lvs.py - the lvs gate (docs/design.md 1.5, "### M4.").

    check_lvs.py --workspace DIR [--out FILE]

Independently re-runs netgen (`eda netgen`, docs/design.md 1.2) comparing
the magic-extracted layout netlist (`harden/runs/run/final/spice/*.spice`,
"NGSPICE file created from *.ext" - LibreLane's own `Magic.SpiceExtraction`
step) against the post-synthesis, post-fill powered netlist
(`final/pnl/*.pnl.v`) plus the PDK's own standard-cell and IO spice models -
the exact same three-circuit recipe LibreLane's own internal `netgen-lvs`
step uses (read from its generated `lvs_script.lvs` once, empirically, to
get the PDK model list right; never re-guessed). LibreLane's own LVS
verdict is not trusted as this gate's answer - the design's point in
separating `lvs` from `harden` at all is a second, independent netgen run,
the same reasoning `timing` and `drc` follow. A from-scratch re-extraction
(`eda magic extract all` on the GDS, bypassing LibreLane's own extraction
step entirely) was tried here first and does not reproduce LibreLane's own
pin naming/property setup closely enough to match even a clean design
(a real gap - this gate is only as independent of `harden`'s own
extraction as that leaves it); `corpus/vde/*/faults/plant_lvs.py`
compensates by corrupting the extracted spice text directly rather than
the GDS, so the fault this gate must catch ("one via removed from the
GDS") is still exercised through this exact comparison.

Passes when netgen reports the two circuits equivalent (gates.yaml's `lvs`
row: "match").

Failure classification (the same three-way split as check_harden.py):
  - no hardened `final/` yet -> CheckError.
  - `eda netgen` times out, crashes, or the report never reaches a decidable
    verdict (neither "match" nor a Property/topology mismatch line) ->
    CheckError - a comparison that did not complete is a refusal, never a
    pass.
  - netgen completes and reports the circuits do not match -> a
    `violations` finding, exit 1.
"""
from __future__ import annotations

import argparse
import glob
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

SCRIPT = "check_lvs"
EDA_BIN = REPO / "bin" / "eda"
TIMEOUT_S = 180.0
# The PDK spice models LibreLane's own netgen-lvs step reads alongside the
# post-synthesis netlist (its own generated lvs_script.lvs, read once to get
# this list - gf180mcuD's standard cells plus the two IO libraries the flow
# always pulls in even for a design that uses no dedicated IO cells).
PDK_SPICE_MODELS = (
    "libs.ref/gf180mcu_fd_sc_mcu7t5v0/spice/gf180mcu_fd_sc_mcu7t5v0.spice",
    "libs.ref/gf180mcu_fd_io/spice/gf180mcu_fd_io.spice",
    "libs.ref/gf180mcu_fd_io/spice/gf180mcu_ef_io.spice",
)


def _pdk_root() -> Path:
    proc = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError(f"eda --print-toolchain-root failed: "
                         f"{proc.stderr.strip()}")
    return Path(proc.stdout.strip()) / "foss" / "pdks"


def _netgen_setup_tcl(pdk_root: Path) -> Path:
    # pdk_root is <T>/foss/pdks; librelane's own copy of this setup script
    # (the one its internal netgen-lvs step reads) lives under <T>'s image
    # site-packages - globbed for the python3.x directory name rather than
    # hardcoded, since that is the one part of the path this box does not
    # pin anywhere else.
    toolchain_root = pdk_root.parents[1]
    matches = glob.glob(str(toolchain_root / "usr" / "local" / "lib" /
                            "python3*" / "dist-packages" / "librelane" /
                            "scripts" / "netgen" / "setup.tcl"))
    if not matches:
        raise CheckError("could not find librelane's netgen/setup.tcl under "
                         f"the toolchain at {toolchain_root}")
    return Path(sorted(matches)[0])


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = ttlib.wrapper_name(spec)
    final_dir = ws / "harden" / "runs" / "run" / "final"
    spice = final_dir / "spice" / f"{top}.spice"
    pnl = final_dir / "pnl" / f"{top}.pnl.v"
    for label, p in (("extracted spice", spice), ("powered netlist", pnl)):
        if not p.is_file():
            raise CheckError(f"no {label} at {p} - has the harden gate run?")

    pdk_root = _pdk_root()
    setup_tcl = _netgen_setup_tcl(pdk_root)
    pdk_dir = pdk_root / ttlib.PDK_NAME
    models = [pdk_dir / rel for rel in PDK_SPICE_MODELS]
    for m in models:
        if not m.is_file():
            raise CheckError(f"no PDK spice model at {m}")

    # work_dir is a scratch dir under ws/log/, never final_dir: final_dir
    # sits inside harden/, the exact directory tree the "harden" artifact
    # kind hashes for freshness (invalidation.yaml) - a scratch file dropped
    # there would change that hash on every lvs run and falsely stale every
    # OTHER gate that also reads "harden" (timing, drc, glsim, precheck,
    # release), including lvs's own last-recorded pass.
    work_dir = ws / "log" / "lvs_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    report_path = work_dir / f".lvs_check_{top}.rpt"
    lvs_script = work_dir / f".lvs_check_{top}.lvs"
    lines = [
        f"set circuit1 [readnet spice {spice}]",
        "set circuit2 [readnet verilog /dev/null]",
        *[f"readnet spice {m} $circuit2" for m in models],
        f"readnet verilog {pnl} $circuit2",
        f'lvs "$circuit1 {top}" "$circuit2 {top}" {setup_tcl} '
        f"{report_path} -blackbox -json",
    ]
    lvs_script.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        proc = subprocess.run(
            [str(EDA_BIN), "netgen", "-batch", f"source {lvs_script}"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"eda netgen timed out after {TIMEOUT_S:g}s: {exc}") from exc

    report_text = (report_path.read_text(encoding="utf-8", errors="replace")
                  if report_path.is_file() else "")
    combined = report_text + "\n" + (proc.stdout or "") + "\n" + (proc.stderr or "")

    matched = "Circuits match uniquely" in combined
    mismatched = any(s in combined for s in (
        "Circuits do not match", "Netlists do not match", "Property errors"))
    if not matched and not mismatched:
        raise CheckError(
            f"netgen produced no decidable LVS verdict (exit {proc.returncode}): "
            f"{combined[-2000:]}")

    violations = []
    if not matched or mismatched:
        violations.append(checklib.violation(
            "lvs", "error", None, top, "netlist_mismatch", [],
            f"netgen: layout-extracted netlist does not match the "
            f"post-synthesis netlist: {combined[-2000:]}", "netgen"))

    payload = checklib.report(SCRIPT, ws / "harden", violations, top=top,
                              matched=matched)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
