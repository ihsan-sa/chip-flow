#!/usr/bin/env python
"""check_mc.py - the mc gate (docs/design.md 1.5, "### M8."): ngspice Monte
Carlo, only when spec.yaml asks for it.

    check_mc.py --workspace DIR [--out FILE]

Pass criteria (gates.yaml): "Yield at or above the spec." Fault: "not
applicable by default" - a block whose spec.yaml carries no `mc` block, or
`mc.enabled: false`, is NOT a stub: this script actually evaluates the spec
and reports `applicable: false` with zero violations, a real pass on a real
question ("does this spec ask for MC?"), never a skip. gate.py's own rule
("a gate whose tool could not run is exit 2, never a pass") is about a tool
that never ran at all - this one ran and answered "not applicable", which is
why no corpus rung needs to carry an `mc` fault (gates.yaml's own `fault:
"not applicable by default"` for this row already says so).

When `mc.enabled: true`, the gf180mcuD models' own mismatch statistics are
already live by default (this box's PDK design.spice: "(default) -
sw_stat_global=1 and sw_stat_mismatch=1" - every `agauss(...)` in a device's
own `.subckt` draws a fresh sample each ngspice invocation with no seed
pinned here), so `mc.runs` independent re-runs of the SAME 'tt'-corner bench
naturally sample device mismatch; yield = the fraction with zero bound
violations, scored against `mc.yield_min`.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import corners as corners_mod  # noqa: E402
import sim_run  # noqa: E402
import simlib  # noqa: E402
import speclib  # noqa: E402

SCRIPT = "check_mc"
DEFAULT_TIMEOUT = 60.0
DEFAULT_RUNS = 20
OUT_SUBDIR = "log/mc"


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    mc_cfg = spec.get("mc") or {}
    top = spec.get("top")

    if not mc_cfg.get("enabled"):
        payload = checklib.report(SCRIPT, ws / "spec" / "spec.yaml", [],
                                  top=top, applicable=False,
                                  reason="spec.yaml has no 'mc.enabled: true'")
        return payload, args.out

    runs = int(mc_cfg.get("runs") or DEFAULT_RUNS)
    yield_min = float(mc_cfg.get("yield_min", 1.0))
    supply = spec.get("supply") or {}
    nominal_vdd = supply.get("vdd")

    eda_bin = sim_run.EDA_BIN
    t_root = sim_run.toolchain_root(eda_bin)
    netlist_path = sim_run.find_netlist(ws)
    tt_corner = corners_mod.corners_by_name(corners_mod.load(), ["tt"])[0]
    sizing = sim_run.load_sizing(ws)
    subs = sim_run.build_subs(t_root, netlist_path, tt_corner, nominal_vdd, sizing)

    out_dir = ws / OUT_SUBDIR
    shutil.rmtree(out_dir, ignore_errors=True)

    outcomes = []
    all_violations = []
    for bench_path, bounds_path in sim_run.find_benches(ws):
        bounds = simlib.load_bounds(bounds_path)
        template_text = bench_path.read_text(encoding="utf-8")
        for i in range(runs):
            bench_name = f"{bench_path.stem}__mc{i}"
            result = sim_run.run_bench_at_corner(
                eda_bin, bench_name, template_text, bounds, subs, tt_corner,
                out_dir, args.timeout, check="mc")
            passed = not result["violations"]
            outcomes.append({"bench": bench_path.name, "run": i, "passed": passed})
            if not passed:
                all_violations.extend(result["violations"])

    total = len(outcomes)
    hits = sum(1 for o in outcomes if o["passed"])
    yield_frac = hits / total if total else 0.0
    violations = []
    if yield_frac < yield_min:
        violations.append(checklib.violation(
            "mc", "error", None, None, "yield_below_spec", [],
            f"Monte Carlo yield {yield_frac:.2%} ({hits}/{total}) is below "
            f"the spec's {yield_min:.2%}", "sim_run"))

    payload = checklib.report(
        SCRIPT, ws / "netlist", violations, top=top, applicable=True,
        runs=total, hits=hits, yield_frac=round(yield_frac, 4),
        yield_min=yield_min, sample_violations=all_violations[:20])
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
