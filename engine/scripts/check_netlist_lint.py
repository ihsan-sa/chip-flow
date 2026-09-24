#!/usr/bin/env python
"""check_netlist_lint.py - the netlist_lint gate (docs/design.md 1.5,
"### M8."). New at M8: no /hwde precedent.

    check_netlist_lint.py --workspace DIR [--out FILE]

Pass criteria (gates.yaml): "Every device uses a gf180mcu_fd_pr model, no
floating node, subcircuit pins match the spec." Two layers:

STATIC (no ngspice call): netlistlib.parse_devices() over netlist/*.cir -
every device's model must be a real subckt this box's own PDK ngspice model
files declare (netlistlib.known_models(), read live off the toolchain, never
a hardcoded snapshot - the fault this catches: "a model not in the PDK");
every refdes spec.yaml's own `devices` list names must actually appear as a
device in the netlist (the "subcircuit pins match the spec" half - a block's
declared device inventory is the spec-side contract check_bench_strength.py
also mutates against); and a floating-node scan (netlistlib.floating_nodes(),
excluding this netlist's own `.subckt` header pins and ground/supply rails).

DYNAMIC ("ngspice dry run"): netlist/*.cir alone is a `.subckt` LIBRARY, not
a runnable deck (nothing instantiates it, so ngspice would never even solve
it) - the same rtl/-vs-tb/ split /vde already uses. The actual dry run reuses
the SAME tb/*.cir bench(es) sim_tt/sim_pvt will run, at the single 'tt'
corner, and scores ONLY for a known ngspice failure signature
(simlib.detect_engine_errors - never bounds; that is sim_tt's job, not
this gate's). This is what a static parse alone cannot catch: this box's own
gf180mcuD models drove a REAL floating gate node through "singular matrix"
-> failed gmin/source stepping -> a "successful", exit-0 `.op` anyway
(simlib.py's own docstring has the transcript) - a purely textual scan
cannot see that ngspice's own DC solve never actually converged cleanly.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import netlistlib  # noqa: E402
import sim_run  # noqa: E402
import speclib  # noqa: E402

SCRIPT = "check_netlist_lint"
DRY_RUN_TIMEOUT = 30.0


def static_violations(ws: Path, spec: dict, netlist_path: Path,
                      pdk_root: Path) -> list[dict]:
    text = netlist_path.read_text(encoding="utf-8")
    rel = str(netlist_path.relative_to(ws))
    devices = netlistlib.parse_devices(text)
    violations = []

    known = netlistlib.known_models(pdk_root)
    for dev in devices:
        if dev["model"] not in known:
            violations.append(checklib.violation(
                "netlist_lint", "error", rel, None, "model_not_in_pdk",
                [dev["ref"]],
                f"{dev['ref']} (line {dev['line']}) uses model "
                f"{dev['model']!r}, not one of the PDK's own ngspice "
                "subcircuits", "netlistlib"))

    declared = spec.get("devices") or []
    device_refs = {d["ref"] for d in devices}
    for ref in declared:
        if ref not in device_refs:
            violations.append(checklib.violation(
                "netlist_lint", "error", rel, None, "declared_device_missing",
                [ref],
                f"spec.yaml declares device {ref!r} but no such instance "
                "exists in the netlist", "netlistlib"))

    declared_pins: set[str] = set()
    for pins in netlistlib.subckt_pins(text).values():
        declared_pins.update(p.lower() for p in pins)
    for node in netlistlib.floating_nodes(devices, declared_pins):
        violations.append(checklib.violation(
            "netlist_lint", "error", rel, None, "floating_node", [node],
            f"node {node!r} is touched by fewer than two device terminals "
            "in the netlist - likely floating", "netlistlib"))

    return violations


def dry_run_violations(ws: Path, timeout: float = DRY_RUN_TIMEOUT) -> list[dict]:
    """Reuse sim_run's own bench materialization at the single 'tt' corner,
    scoring ONLY simlib.detect_engine_errors - bounds are out of scope for
    this gate (sim_tt's own job)."""
    result = sim_run.run_workspace_benches(
        ws, corner_names=["tt"], timeout=timeout, check="netlist_lint")
    violations = []
    for r in result["results"]:
        if r["engine_errors"]:
            tail = r["violations"]
            violations.extend(v for v in tail
                              if v["kind"].startswith("sim_engine_error"))
    return violations


def run(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--timeout", type=float, default=DRY_RUN_TIMEOUT)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    netlist_path = sim_run.find_netlist(ws)
    t_root = sim_run.toolchain_root()
    pdk_root = sim_run.pdk_root(t_root)

    violations = static_violations(ws, spec, netlist_path, pdk_root)
    violations += dry_run_violations(ws, timeout=args.timeout)

    # stamp() hashes exactly this path as input_digest; gate.py's own
    # record_gate cross-checks that against invalidation.yaml's gate_inputs
    # kinds[0] for this gate ("netlist" for ade's netlist_lint).
    payload = checklib.report(SCRIPT, ws / "netlist", violations,
                              top=spec.get("top"),
                              netlist=str(netlist_path.relative_to(ws)))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
