#!/usr/bin/env python
"""check_bench_strength.py - the bench_strength gate (docs/design.md 1.5,
"### M8."): device mutants, analog's answer to /vde's `mutate`
(design.md section 2: "Analog has the same idea in `bench_strength`.").

    check_bench_strength.py --workspace DIR [--out FILE]

For every device spec.yaml declares (`devices`), netlistlib.device_mutants()
builds the mutations gates.yaml names - size doubled, connection removed,
type flipped (netlist/*.cir), bias halved (a plain source in tb/*.cir) - and
each is run, ONE change at a time, against every tb/*.cir bench at the
single 'tt' corner (bench_strength scores the bench's own bounds, not PVT
margin - that is sim_pvt's job). A mutant is KILLED when that run produces
at least one violation (a bound miss, a missing measure, or a real ngspice
engine error - anything sim_run.run_bench_at_corner would report); pass
criteria (gates.yaml): "Every mutant pushes a measure out of bounds" - i.e.
every mutant must be killed. A SURVIVOR (a mutant no bench's bounds catch)
is the fault this gate exists to name: "bounds wide enough to pass
anything".

Each mutant deck carries the block's own sizing/sizing.yaml, exactly as a
sim_tt run's deck does. Before any mutant runs, the UNMUTATED design is run
through the same deck-building against every bench: a baseline that does
not pass its own bounds (an undefined sizing param, a broken bench) would
make every mutant "killed" for a reason that has nothing to do with the
mutation, so the gate refuses (exit 2) instead of scoring it.

Mutated text never touches netlist/ or tb/ on disk - device_mutants()'s
apply() returns a new string, materialized into a scratch deck under
log/bench_strength/ exactly like a real corner run's own deck (sim_run.py's
own log/sim/ convention), so a mutant run can never be mistaken for the
workspace's own recorded netlist/bench state.
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
import netlistlib  # noqa: E402
import sim_run  # noqa: E402
import simlib  # noqa: E402
import speclib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_bench_strength"
DEFAULT_TIMEOUT = 60.0
OUT_SUBDIR = "log/bench_strength"


def run_mutant(eda_bin, ws: Path, mutant: dict, netlist_path: Path,
              netlist_text: str, bench_path: Path, bench_text: str,
              bounds: list[dict], corner: dict, t_root: Path,
              nominal_vdd: float, out_dir: Path, timeout: float,
              sizing: dict) -> dict:
    mutated_netlist_text = (mutant["apply"](netlist_text)
                            if mutant["target"] == "netlist" else netlist_text)
    mutated_bench_text = (mutant["apply"](bench_text)
                          if mutant["target"] == "bench" else bench_text)

    scratch_netlist = out_dir / f"{mutant['id']}__{netlist_path.name}"
    scratch_netlist.write_text(mutated_netlist_text, encoding="utf-8")

    subs = sim_run.build_subs(t_root, scratch_netlist, corner, nominal_vdd,
                              sizing)
    # a distinct, filesystem-safe label per (mutant, bench) pair - the
    # mutant id leads so decks never collide the way a shared bench stem
    # would (run_bench_at_corner names its deck file off this).
    bench_name = f"{mutant['id']}__{bench_path.stem}"
    return sim_run.run_bench_at_corner(
        eda_bin, bench_name, mutated_bench_text, bounds, subs, corner,
        out_dir, timeout, check="bench_strength")


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    eda_bin = sim_run.EDA_BIN
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    devices = spec.get("devices") or []
    if not devices:
        raise CheckError("spec.yaml has no non-empty 'devices' - run "
                         "spec_lint first")
    supply = spec.get("supply") or {}
    nominal_vdd = supply.get("vdd")

    tt_corner = corners_mod.corners_by_name(corners_mod.load(), ["tt"])[0]

    netlist_path = sim_run.find_netlist(ws)
    netlist_text = netlist_path.read_text(encoding="utf-8")
    sizing = sim_run.load_sizing(ws)
    t_root = sim_run.toolchain_root(eda_bin)

    out_dir = ws / OUT_SUBDIR
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)

    by_id: dict[str, dict] = {}
    for bench_path, bounds_path in sim_run.find_benches(ws):
        bench_text = bench_path.read_text(encoding="utf-8")
        bounds = simlib.load_bounds(bounds_path)
        baseline = run_mutant(
            eda_bin, ws, {"id": "baseline", "target": None}, netlist_path,
            netlist_text, bench_path, bench_text, bounds, tt_corner, t_root,
            nominal_vdd, out_dir, args.timeout, sizing)
        if baseline["violations"]:
            first = baseline["violations"][0].get("msg", "")
            raise CheckError(
                f"the unmutated baseline fails {bench_path.name} at tt "
                f"({len(baseline['violations'])} violation(s), first: "
                f"{first}) - a mutant kill would mean nothing; make sim_tt "
                f"pass first (deck: {baseline['deck']})")
        mutants = netlistlib.device_mutants(netlist_text, devices, bench_text)
        for mutant in mutants:
            outcome = run_mutant(
                eda_bin, ws, mutant, netlist_path, netlist_text, bench_path,
                bench_text, bounds, tt_corner, t_root, nominal_vdd, out_dir,
                args.timeout, sizing)
            entry = by_id.setdefault(mutant["id"], {
                "id": mutant["id"], "ref": mutant["ref"], "kind": mutant["kind"],
                "describe": mutant["describe"], "killed": False,
                "benches_tested": []})
            entry["killed"] = entry["killed"] or bool(outcome["violations"])
            entry["benches_tested"].append(bench_path.name)

    if not by_id:
        raise CheckError(
            "no device mutants were generated - check spec.yaml 'devices' "
            "against the netlist's own x-line refdes and tb/'s bias sources")

    total = len(by_id)
    survivors = [m for m in by_id.values() if not m["killed"]]
    violations = [
        checklib.violation(
            "bench_strength", "error", None, None,
            f"survivor_{m['kind']}", [m["ref"]],
            f"mutant {m['id']} ({m['describe']}) survived every bench's "
            "bounds - the bench cannot tell this design apart from a "
            "faulty one", "netlistlib")
        for m in sorted(survivors, key=lambda m: m["id"])
    ]

    payload = checklib.report(
        SCRIPT, ws / "netlist", violations, top=spec.get("top"),
        total_mutants=total, killed=total - len(survivors),
        survived=len(survivors),
        mutants={mid: {"kind": m["kind"], "killed": m["killed"]}
                for mid, m in sorted(by_id.items())})
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
