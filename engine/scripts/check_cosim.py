#!/usr/bin/env python
"""check_cosim.py - the cosim gate (docs/design.md 1.5 msde table, "### M10.").

    check_cosim.py --workspace DIR [--out FILE]

Builds <workspace>/tb's Verilog (the digital side, Icarus-compiled) and runs
its cocotb test(s) - engine/lib/cocotblib.py, same shape check_sim.py uses.
The bench drives an analog block (a SPICE subcircuit) through
cocotbext-ams's MixedSignalBridge (docs/spikes/dcosim.md: ngspice's own
`d_cosim`/`ivlng` bridge is broken in this image; design.md section 5's
first fallback is what M10 builds on) so ONE bench genuinely drives both
sides - the Icarus-compiled digital side reacts to real ngspice-computed
analog transitions, not a stub.

`tb/cosim_bench.json` names the top module, its bounds sidecar, and the
analog netlist it actually simulated:
    {"top": "<hdl_toplevel>", "bounds": "<name>.bounds.json",
     "analog_netlist": "<name>.sp", "analog_kind": "ideal"|"transistor"}
The bench itself is the authority on what it measured: it writes
`reports/cosim_measures.json` (a plain dict of measure name -> value, plus
`digital_toggles` - the count of digital edges it actually observed, backed
by that many `edge_times_ns` entries, not a bare literal - and
`spice_time_reached_ns`, the absolute analog time the bridge actually
synced to, evidence the analog side ran rather than the digital side
toggling on its own) BEFORE any assertion that could raise - a bench that
cannot converge or never sees the digital side toggle still leaves a
measures file behind, so this gate can say exactly what was missing rather
than just "the test failed".

Why this gate does not trust cocotb's own pass/fail, or ngspice's exit code,
alone (docs/design.md, CLAUDE.md: "ngspice exits 0 on many failures, so
parse the measures" / "a gate that did not run is a refusal, never a
pass"): ngspice's shared library (the only way a live cosim bridge can run -
batch `-b` has no bidirectional channel) logs a run's own non-convergence as
free text on its normal callback channel, not as a process exit code -
there is no exit code here at all, only a Python return. So several things
are checked independently, every run, regardless of what cocotb reported:
  1. the raw sim log for ngspice's own failure signatures (a timestep that
     collapsed, a singular matrix, a trouble node) - `ngspice_non_convergence`
  2. the raw sim log for an `.ic` naming a node that does not exist - ngspice
     does not error on this, it logs "IC on non-existent node - <n>,
     ignored" and carries on with that initial condition silently dropped
     (docs/spikes/dcosim.md's own M10 fix was exactly this typo) -
     `ic_ignored`
  3. `reports/cosim_measures.json` exists, and `digital_toggles` is nonzero
     AND backed by that many recorded edge times, not a bare literal -
     `measures_missing` / `digital_side_never_toggled` / `digital_toggles_unbacked`
  4. the bench's own recorded `spice_time_reached_ns` covers its last
     measured edge - evidence the analog bridge actually ran that far,
     not just that the digital side toggled - `spice_time_missing` /
     `spice_time_behind_last_edge`
  5. every measure `tb/*.bounds.json` names is present and inside its bound -
     `measure_missing` / `measure_out_of_bounds`
A cocotb test failure (a bench-side assertion, or the digital side timing
out waiting for an edge that never came) is ALSO reported (`test_failed`),
additively - never instead of the above, since a bench can fail its own
assertion for exactly the reason a measure is out of bounds and both facts
are worth keeping.

CLI/exit contract: checklib's (argparse, JSON to stdout or --out, exit 0
pass, 1 violations, 2 error with a remediation string).
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
from checklib import CheckError  # noqa: E402

SCRIPT = "check_cosim"
EDA_BIN = REPO / "bin" / "eda"
BUILD_SUBDIR = "log/cosim_build"
RESULTS_NAME = "cosim_results.xml"
MEASURES_REL = "reports/cosim_measures.json"
BENCH_META_NAME = "cosim_bench.json"
NGSPICE_LIB_REL = "foss/tools/ngspice/lib"
LIBNGSPICE_REL = "foss/tools/ngspice/lib/libngspice.so.0"
TOOLCHAIN_TIMEOUT_S = 30.0
# A cosim bench runs inside ngspice's shared library, not a subprocess this
# gate can attach a plain `timeout=` to (cocotblib.run_cocotb's own SIGALRM
# wraps this) - generous relative to the observed ~20s real time of the
# ring_osc_div rung, since a shared-library co-sim wedged on a sync point
# that never fires would otherwise hang the gate forever with no exit code
# to catch it on.
COCOTB_TIMEOUT_S = 240.0

# ngspice's own free-text failure signatures on its callback channel - none
# of these change its return value (there isn't one, this runs through the
# shared library), so this is the only place a non-convergent run is
# visible at all. Kept as a short, named list (rather than one broad
# "Error"/"trouble" match) so a benign "warning"-level line never trips it.
NONCONVERGENCE_PATTERNS = [
    re.compile(r"Timestep too small", re.I),
    re.compile(r"trouble with node", re.I),
    re.compile(r"singular matrix", re.I),
    re.compile(r"gmin stepping failed", re.I),
    re.compile(r"doAnalyses:\s*TRAN", re.I),
]

# ngspice does not error on an `.ic` naming a node that does not exist - it
# logs this on the same free-text callback channel and carries on with that
# initial condition silently dropped (docs/spikes/dcosim.md: the wrapper
# netlist cocotbext-ams generates always instantiates the user subcircuit as
# "x1", so an .ic on an internal node's bare name, without that prefix, is
# exactly this - a no-op that looks like a pass until the node in question
# never actually starts where the bench meant it to).
IC_IGNORED_PATTERNS = [
    re.compile(r"IC on non-existent node\s*-\s*([^,]+),\s*ignored", re.I),
]


def toolchain_root(timeout: float = TOOLCHAIN_TIMEOUT_S) -> Path:
    proc = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError("could not resolve the eda toolchain root: "
                         f"{(proc.stderr or proc.stdout).strip()}")
    return Path(proc.stdout.strip())


def ensure_ngspice_env(toolchain: Path) -> None:
    """cocotbext-ams's ngspice backend loads libngspice.so via ctypes, and
    that .so in turn dlopen()s its own codemodel .so's relative to
    LD_LIBRARY_PATH - both must be set before cocotb ever imports
    cocotbext.ams (docs/spikes/dcosim.md's run.sh sets the same two vars for
    the same reason). Set once, additively, never overwriting a caller's
    own LD_LIBRARY_PATH."""
    lib_dir = str(toolchain / NGSPICE_LIB_REL)
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    if lib_dir not in existing.split(":"):
        os.environ["LD_LIBRARY_PATH"] = (
            f"{lib_dir}:{existing}" if existing else lib_dir)
    os.environ.setdefault("LIBNGSPICE_PATH", str(toolchain / LIBNGSPICE_REL))


ANALOG_KINDS = ("ideal", "transistor")


def load_bench_meta(tb_dir: Path) -> dict:
    path = tb_dir / BENCH_META_NAME
    if not path.is_file():
        raise CheckError(f"no {BENCH_META_NAME} at {path} - the bench must "
                         "name its top module and bounds sidecar")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CheckError(f"{path} is not valid JSON: {exc}") from exc
    top = data.get("top")
    if not isinstance(top, str) or not top.strip():
        raise CheckError(f"{path}: no non-empty 'top'")
    bounds_name = data.get("bounds")
    if not isinstance(bounds_name, str) or not bounds_name.strip():
        raise CheckError(f"{path}: no non-empty 'bounds'")
    bounds_path = tb_dir / bounds_name
    if not bounds_path.is_file():
        raise CheckError(f"{path}: bounds sidecar not found at {bounds_path}")
    try:
        bounds = json.loads(bounds_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CheckError(f"{bounds_path} is not valid JSON: {exc}") from exc
    if not isinstance(bounds, dict) or not bounds:
        raise CheckError(f"{bounds_path}: no non-empty bounds mapping")
    # Which SPICE netlist this bench actually simulated, and whether it is
    # an ideal behavioral stand-in or a real transistor-level block - a
    # reader of the report should not have to open tb/ to tell the two
    # apart (docs/spikes/dcosim.md: ring_osc_div's own ring5.sp is
    # deliberately ideal RC/comparator stages, not a gf180 ring, precisely
    # so this distinction matters).
    netlist_name = data.get("analog_netlist")
    if not isinstance(netlist_name, str) or not netlist_name.strip():
        raise CheckError(f"{path}: no non-empty 'analog_netlist' - which "
                         "SPICE netlist this bench actually simulated")
    if not (tb_dir / netlist_name).is_file():
        raise CheckError(f"{path}: analog_netlist {netlist_name!r} not "
                         f"found under {tb_dir}")
    analog_kind = data.get("analog_kind")
    if analog_kind not in ANALOG_KINDS:
        raise CheckError(f"{path}: 'analog_kind' must be one of "
                         f"{ANALOG_KINDS}, got {analog_kind!r}")
    return {"top": top, "bounds": bounds, "bounds_path": bounds_path,
           "analog_netlist": netlist_name, "analog_kind": analog_kind}


def collect_sources(tb_dir: Path) -> list[Path]:
    files = sorted(tb_dir.glob("*.v")) + sorted(tb_dir.glob("*.sv"))
    if not files:
        raise CheckError(f"no .v/.sv files under {tb_dir}")
    return files


def scan_nonconvergence(log_text: str) -> list[str]:
    hits = []
    for pat in NONCONVERGENCE_PATTERNS:
        if pat.search(log_text):
            hits.append(pat.pattern)
    return hits


def scan_ic_ignored(log_text: str) -> list[str]:
    """Node names ngspice's own sim log names as an `.ic` target it could
    not find - it does not error, only logs and carries on, so this is the
    only place a dropped initial condition is visible at all."""
    hits: list[str] = []
    for pat in IC_IGNORED_PATTERNS:
        hits.extend(m.group(1).strip() for m in pat.finditer(log_text))
    return hits


def evaluate_measures(measures: dict | None, bounds: dict) -> list[dict]:
    """The pure, unit-testable half of this gate: given what the bench
    measured (or None - the file never appeared) and the bounds sidecar,
    return the violations. No workspace, no subprocess, no ngspice -
    tests/test_check_cosim.py exercises this directly for the fault shapes
    CLAUDE.md names ("a non-converging run, a bench where the digital side
    never toggled, or a missing measure") plus two evidence checks: a bench
    could otherwise write `digital_toggles: 8` as a bare literal (no
    analog side needed to produce it), or drive a purely digital counter
    with the analog bridge never actually running - `digital_toggles_unbacked`
    and `spice_time_missing`/`spice_time_behind_last_edge` close those."""
    out = []

    def bad(kind, refs, msg):
        out.append(checklib.violation(
            "cosim", "error", MEASURES_REL, None, kind, refs, msg,
            "check_cosim"))

    if measures is None:
        bad("measures_missing", [], f"{MEASURES_REL} was never written - "
           "the bench did not run to the point of recording a result")
        return out

    toggles = measures.get("digital_toggles")
    edge_times = measures.get("edge_times_ns")
    edges = (edge_times if isinstance(edge_times, list) and all(
        isinstance(t, (int, float)) and not isinstance(t, bool)
        for t in edge_times) else None)

    if (not isinstance(toggles, (int, float)) or isinstance(toggles, bool)
            or toggles <= 0):
        bad("digital_side_never_toggled", [],
           f"digital_toggles is {toggles!r} - the digital side never acted "
           "on the analog waveform")
    elif edges is None or len(edges) != toggles:
        bad("digital_toggles_unbacked", [],
           f"digital_toggles is {toggles!r} but edge_times_ns is "
           f"{edge_times!r} - a toggle count must be backed by that many "
           "recorded edges, not a bare literal")

    # spice_time_reached_ns is the bench's own record of how far the analog
    # bridge's SPICE simulation actually got (cocotbext-ams's own
    # _last_sync_spice_time, updated only from the analog side's ngspice
    # thread) - evidence the analog side genuinely ran, not just that the
    # digital side toggled on its own with no real analog block behind it.
    spice_time = measures.get("spice_time_reached_ns")
    if not isinstance(spice_time, (int, float)) or isinstance(spice_time, bool):
        bad("spice_time_missing", [],
           f"spice_time_reached_ns is {spice_time!r} - no evidence the "
           "analog bridge actually ran")
    elif edges:
        last_edge = max(edges)
        if spice_time < last_edge:
            bad("spice_time_behind_last_edge", [],
               f"spice_time_reached_ns={spice_time!r} is behind the last "
               f"measured edge at {last_edge!r} ns - the analog side never "
               "reached the point the digital side claims to have toggled at")

    for name, bound in sorted(bounds.items()):
        if name not in measures or measures.get(name) is None:
            bad("measure_missing", [name],
               f"{name!r} has a bound but no recorded value")
            continue
        value = measures[name]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            bad("measure_missing", [name],
               f"{name!r} recorded a non-numeric value: {value!r}")
            continue
        lo, hi = bound.get("min"), bound.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            bad("measure_out_of_bounds", [name],
               f"{name!r} = {value!r} is outside its bound [{lo}, {hi}]")

    return out


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    tb_dir = ws / "tb"
    # invalidation.yaml's gate_inputs.msde.cosim lists `interface` first -
    # gate.py cross-checks a recorded result's input_digest against THAT
    # kind's current hash (state.py record_gate: "kinds[0]"), so this
    # report must be stamped against interface.yaml, not tb/, or every real
    # recording would raise a digest mismatch. interface.yaml also has to
    # exist by the time cosim runs regardless (P1 split precedes P3
    # integrate, docs/design.md 1.4's msde phase order).
    interface_path = ws / "interface.yaml"
    if not interface_path.is_file():
        raise CheckError(f"no interface.yaml at {interface_path} - split "
                         "must run (and pass) before cosim")
    meta = load_bench_meta(tb_dir)
    sources = collect_sources(tb_dir)
    modules = cocotblib.test_modules(tb_dir)
    if not modules:
        raise CheckError(f"no test_*.py modules under {tb_dir}")

    ensure_ngspice_env(toolchain_root())

    # A stale measures.json from a previous run must never be mistaken for
    # this run's result - if this run crashes before the bench's own
    # finally-block writes one, that must read as "missing", not "old".
    measures_path = ws / MEASURES_REL
    measures_path.unlink(missing_ok=True)

    build_dir = ws / BUILD_SUBDIR
    shutil.rmtree(build_dir, ignore_errors=True)
    results_xml = ws / "log" / RESULTS_NAME
    xml_path = cocotblib.run_cocotb(build_dir, tb_dir, sources, meta["top"],
                                    modules, results_xml,
                                    timeout_s=COCOTB_TIMEOUT_S)
    results = cocotblib.parse_results_xml(xml_path)
    if not results:
        raise CheckError(f"cocotb produced no test cases in {xml_path}")

    log_text = (build_dir / "sim.log").read_text(
        encoding="utf-8", errors="replace") if (build_dir / "sim.log").is_file() else ""
    nonconv = scan_nonconvergence(log_text)
    ic_ignored = scan_ic_ignored(log_text)

    violations = []
    if nonconv:
        violations.append(checklib.violation(
            "cosim", "error", "log/sim.log", None, "ngspice_non_convergence",
            [], "ngspice logged a non-convergence signature during this "
            f"run: {', '.join(sorted(set(nonconv)))}", "ngspice"))
    if ic_ignored:
        nodes = sorted(set(ic_ignored))
        violations.append(checklib.violation(
            "cosim", "error", "log/sim.log", None, "ic_ignored", nodes,
            "ngspice ignored an .ic on a node that does not exist: "
            f"{', '.join(nodes)} - the initial condition it named was "
            "silently dropped, not applied", "ngspice"))

    measures = None
    if measures_path.is_file():
        try:
            measures = json.loads(measures_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CheckError(f"{measures_path} is not valid JSON: {exc}") from exc
    violations.extend(evaluate_measures(measures, meta["bounds"]))

    for name, res in sorted(results.items()):
        if res["passed"]:
            continue
        if res.get("skipped"):
            violations.append(checklib.violation(
                "cosim", "error", None, name, "test_skipped", [],
                f"{name} was skipped - it never ran", "cocotb"))
            continue
        violations.append(checklib.violation(
            "cosim", "error", None, name, "test_failed", [],
            f"{name} failed: {res['message'] or 'see the sim log'}", "cocotb"))

    payload = checklib.report(SCRIPT, interface_path, violations, top=meta["top"],
                              analog_netlist=meta["analog_netlist"],
                              analog_kind=meta["analog_kind"],
                              measures=measures,
                              tests_run=sorted(results),
                              ngspice_signatures=sorted(set(nonconv)),
                              ic_ignored_nodes=sorted(set(ic_ignored)))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
