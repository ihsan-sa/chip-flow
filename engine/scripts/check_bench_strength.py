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
every mutant must be killed, bar the per-mutant rulings below. A SURVIVOR
(a mutant no bench's bounds catch) is the fault this gate exists to name:
"bounds wide enough to pass anything".

Each mutant deck carries the block's own sizing/sizing.yaml, exactly as a
sim_tt run's deck does. Before any mutant runs, the UNMUTATED design is run
through the same deck-building against every bench: a baseline that does
not pass its own bounds (an undefined sizing param, a broken bench) would
make every mutant "killed" for a reason that has nothing to do with the
mutation, so the gate refuses (exit 2) instead of scoring it.

A survivor fails the gate unless the owner has ruled on that one mutant in
the workspace's spec/mutant_rulings.yaml (engine/lib/rulingslib.py; there is
no class-wide exception). Two rulings exist. `equivalent` accepts a survivor
no bench can observe, with who ruled and the evidence; it is reported at
severity "info" as `equivalent_<kind>`. `below_spread` accepts a survivor
whose effect is smaller than the design's own process spread, and the gate
checks it against its own run rather than trusting it: the entry's
netlist_line must be a line of the netlist (the bench, for a bias mutant)
naming the mutant's device, its measure must be one the benches bound, its
delta must match the gate's measured (mutant - baseline) / baseline within
0.001 + 5%, and EVERY tt measure the mutant moves must sit inside its own
spread, max(3 sigma_m, 2%) - the rule the tt bounds themselves use - where
sigma_m is the entry's `sigma` for the named measure and its optional
`sigmas` map (measure -> relative mc 1-sigma) for the rest; a measure given
no sigma is held to the 2% floor. Naming one measure never hides a larger
move in another: that other measure is checked against its own spread. A
valid entry is reported at "info" as `below_spread_<kind>`. An entry that fails any check, or names a mutant that
was killed or was never generated, is refused (exit 2): a stale ruling must
not sit silently. Every survivor's per-measure deltas are in the `mutants`
facts, so an entry can be written from one run.

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
import rulingslib  # noqa: E402
import sim_run  # noqa: E402
import simlib  # noqa: E402
import speclib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_bench_strength"
DEFAULT_TIMEOUT = 60.0

OUT_SUBDIR = "log/bench_strength"
# below_spread rulings (rulingslib): a recorded delta may differ from the
# measured one by DELTA_ABS_TOL + DELTA_REL_TOL*|measured|, and the measured
# |delta| must sit inside max(SIGMA_K*sigma, SPREAD_FLOOR).
DELTA_ABS_TOL = 0.001
DELTA_REL_TOL = 0.05
SIGMA_K = 3.0
SPREAD_FLOOR = 0.02


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


def violation_key(v: dict) -> tuple:
    """What makes two sim findings the same finding: kind, severity and
    refs (measure and corner)."""
    return (v.get("kind"), v.get("severity"), tuple(v.get("refs") or ()))


def _lines(text: str) -> set[str]:
    return {" ".join(ln.split()) for ln in text.splitlines() if ln.strip()}


def spread_limit(sigma: float) -> float:
    """The tt bounds' own rule: nominal +/- max(3 sigma, 2%)."""
    return max(SIGMA_K * sigma, SPREAD_FLOOR)


def relative_deltas(baselines: dict[str, dict], measures: dict[str, dict],
                    tt_names: dict[str, set]) -> dict[str, float]:
    """{measure: (mutant - baseline) / baseline} over every measure a bench
    bounds at tt; a measure whose baseline is 0 or missing (or the mutant
    never printed) is skipped. A name two benches share keeps the larger
    |delta|."""
    out: dict[str, float] = {}
    for bench, mm in measures.items():
        base = baselines.get(bench) or {}
        for name in sorted(tt_names.get(bench, ())):
            b, v = base.get(name), mm.get(name)
            if b is None or v is None or b == 0:
                continue
            d = (v - b) / b
            if name not in out or abs(d) > abs(out[name]):
                out[name] = d
    return out


def check_below_spread(ruling: dict, mutant: dict, tt_measures: set[str],
                       source_lines: set[str]) -> None:
    """Refuse (CheckError) a below_spread ruling the gate's own run does
    not bear out; return None when it holds."""
    mid = mutant["id"]
    where = f"{rulingslib.RULINGS_REL} below_spread {mid!r}"
    line = " ".join(ruling["netlist_line"].split())
    if line not in source_lines:
        raise CheckError(f"{where}: netlist_line {ruling['netlist_line']!r} "
                         f"is not a line of the block's "
                         f"{'bench' if mutant['target'] == 'bench' else 'netlist'}"
                         " - copy the device's line as it stands")
    if line.split()[0].lower() != str(mutant["ref"]).lower():
        raise CheckError(f"{where}: netlist_line names "
                         f"{line.split()[0]!r}, not the mutated device "
                         f"{mutant['ref']!r}")
    measure = ruling["measure"].lower()
    if measure not in tt_measures:
        raise CheckError(f"{where}: measure {ruling['measure']!r} is not a "
                         "tt measure the benches bound (have: "
                         f"{', '.join(sorted(tt_measures))})")
    deltas = mutant["deltas"]
    if measure not in deltas:
        raise CheckError(f"{where}: the gate could not measure a relative "
                         f"delta for {ruling['measure']!r} (baseline 0 or "
                         "missing) - it cannot be ruled below spread")
    measured = deltas[measure]
    if abs(ruling["delta"] - measured) > DELTA_ABS_TOL + DELTA_REL_TOL * abs(
            measured):
        raise CheckError(f"{where}: recorded delta {ruling['delta']} does "
                         f"not match the measured {measured:.6g} - rewrite "
                         "it from this run's mutants facts")
    sigmas = {k.lower(): v for k, v in (ruling.get("sigmas") or {}).items()}
    for name in sigmas:
        if name not in tt_measures:
            raise CheckError(f"{where}: sigmas names {name!r}, which is not "
                             "a tt measure the benches bound")
    sigmas[measure] = ruling["sigma"]
    for name in sorted(deltas):
        if name == measure:
            continue
        # a measure given no sigma is held to the 2% floor
        other = spread_limit(sigmas.get(name, 0.0))
        if abs(deltas[name]) > other:
            raise CheckError(f"{where}: {name!r} moved {deltas[name]:.6g}, "
                             f"outside its own max(3 sigma, 2%) = "
                             f"{other:.6g} - a real effect a bench must "
                             "catch, or give that measure's mc sigma in "
                             "`sigmas`")
    limit = spread_limit(ruling["sigma"])
    if abs(measured) > limit:
        raise CheckError(f"{where}: measured delta {measured:.6g} is outside "
                         f"max(3 sigma, 2%) = {limit:.6g} - this mutant is "
                         "a real effect a bench must catch, not spread")


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

    rulings = rulingslib.load(ws, "bench_strength")

    tt_corner = corners_mod.corners_by_name(corners_mod.load(), ["tt"])[0]

    netlist_path = sim_run.find_netlist(ws)
    netlist_text = netlist_path.read_text(encoding="utf-8")
    sizing = sim_run.load_sizing(ws)
    t_root = sim_run.toolchain_root(eda_bin)

    out_dir = ws / OUT_SUBDIR
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)

    by_id: dict[str, dict] = {}
    baselines: dict[str, dict] = {}   # bench name -> its tt measures
    tt_names: dict[str, set] = {}     # bench name -> measures bound at tt
    bench_texts: list[str] = []
    for bench_path, bounds_path in sim_run.find_benches(ws):
        bench_text = bench_path.read_text(encoding="utf-8")
        bench_texts.append(bench_text)
        bounds = simlib.load_bounds(bounds_path)
        tt_names[bench_path.name] = {
            str(b["measure"]).lower() for b in bounds
            if b.get("corners", "all") == "all" or "tt" in b["corners"]}
        baseline = run_mutant(
            eda_bin, ws, {"id": "baseline", "target": None}, netlist_path,
            netlist_text, bench_path, bench_text, bounds, tt_corner, t_root,
            nominal_vdd, out_dir, args.timeout, sizing)
        # sim_tt passes with warning-severity bound misses, so only an
        # error refuses; a warning the baseline already has cannot count
        # as a kill below, or every mutant would be "killed" by it.
        base_errors = [v for v in baseline["violations"]
                       if v.get("severity") == "error"]
        if base_errors:
            first = base_errors[0].get("msg", "")
            raise CheckError(
                f"the unmutated baseline fails {bench_path.name} at tt "
                f"({len(base_errors)} error(s), first: "
                f"{first}) - a mutant kill would mean nothing; make sim_tt "
                f"pass first (deck: {baseline['deck']})")
        base_keys = {violation_key(v) for v in baseline["violations"]}
        baselines[bench_path.name] = baseline.get("measures") or {}
        mutants = netlistlib.device_mutants(netlist_text, devices, bench_text)
        for mutant in mutants:
            outcome = run_mutant(
                eda_bin, ws, mutant, netlist_path, netlist_text, bench_path,
                bench_text, bounds, tt_corner, t_root, nominal_vdd, out_dir,
                args.timeout, sizing)
            entry = by_id.setdefault(mutant["id"], {
                "id": mutant["id"], "ref": mutant["ref"], "kind": mutant["kind"],
                "target": mutant["target"],
                "describe": mutant["describe"], "killed": False,
                "benches_tested": [], "measures": {}})
            entry["killed"] = entry["killed"] or any(
                violation_key(v) not in base_keys
                for v in outcome["violations"])
            entry["benches_tested"].append(bench_path.name)
            entry["measures"][bench_path.name] = outcome.get("measures") or {}

    if not by_id:
        raise CheckError(
            "no device mutants were generated - check spec.yaml 'devices' "
            "against the netlist's own x-line refdes and tb/'s bias sources")

    total = len(by_id)
    survivors = [m for m in by_id.values() if not m["killed"]]
    for m in survivors:
        m["deltas"] = relative_deltas(baselines, m["measures"], tt_names)

    equivalent = rulings["equivalent"]
    below = rulings["below_spread"]
    for name, entries in (("equivalent", equivalent),
                          ("below_spread", below)):
        for mid in entries:
            if mid not in by_id:
                raise CheckError(
                    f"{rulingslib.RULINGS_REL} {name} names mutant {mid!r}, "
                    "which this run did not generate - remove the stale "
                    "ruling (mutant ids: "
                    f"{', '.join(sorted(by_id))})")
            if by_id[mid]["killed"]:
                raise CheckError(
                    f"{rulingslib.RULINGS_REL} {name} names mutant {mid!r}, "
                    "which a bench now kills - remove the stale ruling")
    all_tt = set().union(*tt_names.values()) if tt_names else set()
    netlist_lines = _lines(netlist_text)
    bench_lines = set().union(*(_lines(t) for t in bench_texts))
    for mid, ruling in below.items():
        check_below_spread(ruling, by_id[mid], all_tt,
                           bench_lines if by_id[mid]["target"] == "bench"
                           else netlist_lines)

    violations = []
    for m in sorted(survivors, key=lambda m: m["id"]):
        if m["id"] in equivalent:
            r = equivalent[m["id"]]
            violations.append(checklib.violation(
                "bench_strength", "info", None, None,
                f"equivalent_{m['kind']}", [m["ref"]],
                f"mutant {m['id']} ({m['describe']}) survived and is "
                f"accepted as equivalent by ruling ({r['ruling']}): "
                f"{r['evidence']}", "mutant_rulings"))
        elif m["id"] in below:
            r = below[m["id"]]
            measured = m["deltas"][r["measure"].lower()]
            violations.append(checklib.violation(
                "bench_strength", "info", None, None,
                f"below_spread_{m['kind']}", [m["ref"]],
                f"mutant {m['id']} ({m['describe']}) survived and is "
                f"accepted as below process spread by ruling "
                f"({r['ruling']}): {r['measure']} moved {measured:+.4g} "
                f"(relative) against sigma {r['sigma']:.4g}, inside "
                f"max(3 sigma, 2%) = {spread_limit(r['sigma']):.4g}",
                "mutant_rulings"))
        else:
            violations.append(checklib.violation(
                "bench_strength", "error", None, None,
                f"survivor_{m['kind']}", [m["ref"]],
                f"mutant {m['id']} ({m['describe']}) survived every bench's "
                "bounds - the bench cannot tell this design apart from a "
                "faulty one", "netlistlib"))

    def fact(m: dict) -> dict:
        f = {"kind": m["kind"], "killed": m["killed"],
             "describe": m["describe"]}
        if not m["killed"]:
            f["deltas"] = {k: round(v, 6) for k, v in sorted(m["deltas"].items())}
        return f

    payload = checklib.report(
        SCRIPT, ws / "netlist", violations, top=spec.get("top"),
        total_mutants=total, killed=total - len(survivors),
        survived=len(survivors),
        equivalent=len(equivalent), below_spread=len(below),
        equivalent_ids=sorted(equivalent), below_spread_ids=sorted(below),
        mutants={mid: fact(m) for mid, m in sorted(by_id.items())})
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
