#!/usr/bin/env python
"""check_bench_strength.py - the bench_strength gate (docs/design.md 1.5,
"### M8."): device mutants, analog's answer to /vde's `mutate`
(design.md section 2: "Analog has the same idea in `bench_strength`.").

    check_bench_strength.py --workspace DIR [--jobs auto|N] [--out FILE]

For every device spec.yaml declares (`devices`), netlistlib.device_mutants()
builds the mutations gates.yaml names - size doubled, connection removed,
type flipped (netlist/*.cir), bias halved (a plain source in tb/*.cir) - and
each is run, ONE change at a time, against the tb/*.cir benches at the
single 'tt' corner (bench_strength scores the bench's own bounds, not PVT
margin - that is sim_pvt's job). A bound counts at tt when its `corners`
list names that corner by any of its names (corners.names_of: `tt`, or
`tt_27c` as a spec grid and so sim_pvt call it). A mutant is KILLED when that run produces
at least one violation the baseline does not (a bound miss, a missing
measure, or a real ngspice engine error - anything
sim_run.run_bench_at_corner would report), or when it moves a tt measure
past that measure's declared `sensitivity`; pass criteria (gates.yaml):
"Every mutant pushes a measure out of bounds" - i.e. every mutant must be
killed, bar the per-mutant rulings below. A SURVIVOR (a mutant no bench
catches) is the fault this gate exists to name: "bounds wide enough to
pass anything".

A mutant runs its benches cheapest first - by the unmutated baseline's own
measured runtime, to 0.1 s, ties by name; the order is the `bench_order`
fact - and stops at the first bench that kills it, which its fact names
as `killed_by`. A killed mutant therefore never runs the benches after
that one; a survivor runs them all, so its deltas cover every bench.
Mutants run in a worker pool of --jobs (default ${CHIP_FLOW_BENCH_JOBS},
else "auto") at once: "auto" is max(1, cores - 1-minute load), re-read
before each mutant starts, with this pool's own sims that have run long
enough to be in the load average taken back out of it, so a busy box
gets 1. Each mutant's result depends on that mutant alone, and the facts
come out in id order, so any --jobs gives the same report as --jobs 1.

A declared behavioural `b...` source in netlist/*.cir gets its own two
mutants (netlistlib.device_mutants): output_stuck (its expression replaced
by 0) and gain_halved (its expression times 0.5). Coverage is reported
over every device, not only the mutated ones: `devices_total` counts each
element of the netlist (netlistlib.parse_elements - every element letter,
so B/E/G/F/H sources, primitives and non-PDK `x` instances too) plus each
declared ref outside it, `devices_mutated` the ones at least one mutant
ran for, and `unmutated` lists the rest as [{device, kind, reason}]. A
device in that list never enters `total_mutants`, `killed` or `survived`,
because no mutant of it ran. A DECLARED device in it (spec.yaml `devices`
names it, but no mutation class covers it: an E/G/F/H source, a B source
whose expression runs onto a `+` line or has no clear extent, a bench
source that is not a plain numeric `i`/`v`, a ref found nowhere) is an
error, `device_not_mutated`: the spec made it accountable and the gate
could not test it, so the gate fails. An UNDECLARED netlist element is a
warning, `device_undeclared`: spec.yaml `devices` is the owner's
accountable set (a block may leave a device out with a written reason, as
bandgap's startup network is), so it does not fail the gate, but it is
named so a kill tally never reads as covering the whole netlist.

A bench's bounds must equal the spec's (speclib, bench_bound_value_mismatch),
so a mutant that moves a measure a long way but stays inside the spec would
otherwise be a survivor no bench-writer could legally kill. A bound in
tb/*.bounds.json may therefore carry `sensitivity`: the relative move,
|(mutant - baseline) / baseline| against the UNMUTATED design's own tt
value, past which the bench counts the mutant as told apart. It is not a
pass bound - sim_tt, sim_pvt and spec_lint never read it, so a design that
meets the spec still passes them - and it only ever adds kills here. The
gate refuses (exit 2) a sensitivity below 2%, the tt bounds' own spread
floor, and one on a bound not scored at tt; the bench-writer should
declare max(3 sigma, 2%) of the measure's mc spread when it is known. It
also refuses a sensitivity on a measure whose unmutated tt value is below
2% of its own bound's magnitude (or 0): a relative move of a near-zero
value is the simulator's absolute tolerance, not the mutant. A measure
the mutant never printed cannot kill this way.

Such a near-zero measure (a low level, an off current, a stop time far
inside its bound) may instead carry `sensitivity_abs` with
`sensitivity_unit` ("s", "V" or "A"): the absolute move |mutant -
baseline|, in the measure's own units, at or past which the mutant is told
apart. It is no pass bound either, and it only scores a measure near zero
in that bench - on any other it is a no-op, so it adds kills only where the
relative rule is refused. The gate refuses (exit 2) one below the
simulator's own floor, read from the bench and netlist text (abs_floor): a
time is held to the transient's effective max step (its tmax, else
ngspice's min(tstep, span/50)), a voltage to vntol + reltol x the bound's
magnitude, a current to abstol + reltol x it, from the deck's `.options`
or ngspice's defaults (no `.options` file outside the bench and the
netlist is read); a deck whose tran line or options are not plain numbers,
or a time sensitivity with no transient, is refused too. Each sensitivity
kill is in the `mutants` facts as `sensitivity_kill` (bench, measure,
delta, sensitivity, kind "relative" or "absolute", and unit for an absolute
one), counted in `killed_by_sensitivity` and split by kind in
`killed_by_sensitivity_kind`. A survivor's facts carry `abs_deltas`,
mutant - baseline for each near-zero measure, to size one from.

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
0.001 + 5%, and every tt measure the mutant moves must sit inside
its own spread, max(3 sigma_m, 2%) - the rule the tt bounds themselves use
- where sigma_m is the entry's `sigma` for the named measure and its
optional `sigmas` map (measure -> relative mc 1-sigma) for the rest; a
measure given no sigma is held to the 2% floor. Naming one measure never
hides a larger move in another, bar one exemption: a measure whose
unmutated tt value is near zero - under 2% of its bound's magnitude in
every bench that bounds it, the same rule that refuses a sensitivity on it
- is not held to spread, since its relative move is simulator tolerance.
That exemption hides no failure: rulings are only checked for survivors,
and a mutant that pushes any measure, near zero or not, past a bound its
baseline met is already killed. A
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
import math
import os
import shutil
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
# the worker pool (run_pool): --jobs, else this env var, else "auto".
JOBS_ENV = "CHIP_FLOW_BENCH_JOBS"
# a worker that has run this long is counted in the 1-minute load average
# (1 - e^-3 = 95% of it), so auto_jobs() takes it back out of the load.
SETTLED_S = 180.0
# how often a pool waiting on its workers re-reads the load, in seconds.
POLL_S = 15.0


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


def parse_jobs(text: str) -> int | None:
    """--jobs / CHIP_FLOW_BENCH_JOBS: "auto" (None) or a worker count >= 1."""
    if str(text).strip().lower() == "auto":
        return None
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise CheckError(f"--jobs {text!r} is neither 'auto' nor a whole "
                         f"number >= 1 (also read from ${JOBS_ENV})")
    return n


def box_cores() -> int:
    return os.cpu_count() or 1


def box_load1() -> float:
    return os.getloadavg()[0]


def auto_jobs(cores: int, load1: float, settled: int) -> int:
    """The 'auto' cap: max(1, cores - 1-minute load), floored, and never
    more than `cores`. The load counts this pool's own sims too, so the
    `settled` workers - running SETTLED_S or longer, so in the load
    average - are taken back out of it; a younger one is still counted
    against the cap twice (in the load and in the pool), which errs to
    fewer workers, never more. A box at load 13 on 6 cores gets 1."""
    # "capped to what the box has free: max(1, cores - 1-minute load)"
    return max(1, min(cores, math.floor(cores - load1 + settled)))


def run_pool(tasks: list, fn, jobs: int | None, *, cores=box_cores,
             load1=box_load1, clock=time.monotonic) -> list:
    """[fn(t) for t in tasks], in task order, with up to `jobs` running at
    once - or, for jobs None ('auto'), auto_jobs() re-read before every
    start and every POLL_S while waiting, so a box that gets busy stops
    being handed more. jobs 1 runs them in this thread, one after another.
    fn's first exception is raised once the workers already running end."""
    if jobs == 1 or len(tasks) <= 1:
        return [fn(t) for t in tasks]
    results: list = [None] * len(tasks)
    with ThreadPoolExecutor(max_workers=jobs or cores()) as ex:
        running: dict = {}   # future -> (task index, start time)
        nxt = 0
        while nxt < len(tasks) or running:
            if jobs:
                cap = jobs
            else:
                now = clock()
                cap = auto_jobs(cores(), load1(), sum(
                    1 for _, t0 in running.values() if now - t0 >= SETTLED_S))
            while nxt < len(tasks) and len(running) < cap:
                running[ex.submit(fn, tasks[nxt])] = (nxt, clock())
                nxt += 1
            done, _ = wait(running, timeout=POLL_S,
                           return_when=FIRST_COMPLETED)
            for fut in done:
                idx, _t0 = running.pop(fut)
                results[idx] = fut.result()
    return results


def score_mutant(plan: list[tuple[str, dict]], ctx: dict, run_args: dict,
                 bench_names: list[str]) -> dict:
    """Run one mutant through its benches in `plan` order (cheapest
    baseline first) and stop at the first bench that kills it: a violation
    its baseline does not have, else a move past a declared sensitivity.
    `killed_by` names that bench. A survivor ran every bench, so its
    `measures` hold all of them, in `bench_names` (alphabetical) order."""
    first = plan[0][1]
    entry = {"id": first["id"], "ref": first["ref"], "kind": first["kind"],
             "target": first["target"], "describe": first["describe"],
             "killed": False, "benches_tested": [], "measures": {}}
    # "Stop simulating a mutant once one bench has killed it"
    for name, mutant in plan:
        c = ctx[name]
        outcome = run_mutant(
            run_args["eda_bin"], run_args["ws"], mutant,
            run_args["netlist_path"], run_args["netlist_text"], c["path"],
            c["text"], c["bounds"], run_args["corner"], run_args["t_root"],
            run_args["nominal_vdd"], run_args["out_dir"],
            run_args["timeout"], run_args["sizing"])
        entry["benches_tested"].append(name)
        entry["measures"][name] = outcome.get("measures") or {}
        if any(violation_key(v) not in c["base_keys"]
               for v in outcome["violations"]):
            entry["killed"], entry["killed_by"] = True, name
            break
        hit = sensitivity_kill(c["baseline"], outcome.get("measures") or {},
                               c["sens"], c["abs_sens"])
        if hit:
            entry["killed"], entry["killed_by"] = True, name
            entry["sensitivity_kill"] = {"bench": name, **hit}
            break
    entry["measures"] = {b: entry["measures"][b] for b in bench_names
                         if b in entry["measures"]}
    return entry


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


def bench_sensitivities(bounds: list[dict], bench: str,
                        tt_measures: set[str]) -> dict[str, float]:
    """{measure: declared sensitivity} for one bench's bounds sidecar. A
    sensitivity is refused (CheckError) on a bound not scored at tt - the
    gate runs tt only, so it would never be used - and below
    SPREAD_FLOOR, the smallest relative move the tt bounds' own rule
    counts as more than spread."""
    out: dict[str, float] = {}
    for b in bounds:
        if "sensitivity" not in b:
            continue
        name = str(b["measure"]).lower()
        s = float(b["sensitivity"])
        if name not in tt_measures:
            raise CheckError(
                f"tb/{bench}: {b['measure']!r} declares a sensitivity but is "
                "not scored at tt - bench_strength runs tt only; drop it")
        if s < SPREAD_FLOOR:
            raise CheckError(
                f"tb/{bench}: {b['measure']!r} sensitivity {s:g} is below "
                f"the {SPREAD_FLOOR:g} floor - a smaller relative move is "
                "not told apart from spread; declare at least "
                f"max(3 sigma, {SPREAD_FLOOR:g})")
        out[name] = s
    return out


def bound_scale(bound: dict) -> float:
    """A bound's magnitude: the larger of |min| and |max| (0 if neither)."""
    return max(abs(bound.get(k) or 0.0) for k in ("min", "max"))


def near_zero(value: float | None, scale: float) -> bool:
    """True when an unmutated tt value is missing, 0, or below SPREAD_FLOOR
    of its bound's magnitude `scale`: a relative move of such a value is the
    simulator's absolute tolerance, not a mutant. The one near-zero rule
    both the sensitivity path and below_spread rulings use."""
    return value is None or value == 0 or abs(value) < SPREAD_FLOOR * scale


def near_zero_measures(bounds_by_bench: dict[str, list[dict]],
                       baselines: dict[str, dict],
                       tt_names: dict[str, set]) -> set[str]:
    """The tt measures whose unmutated value is near_zero() against every
    bound on them in every bench that bounds them at tt. A name near zero in
    one bench but not in another is not in the set, so its real move in the
    other bench is still held to spread."""
    verdict: dict[str, bool] = {}
    for bench, bounds in bounds_by_bench.items():
        base = baselines.get(bench) or {}
        for b in bounds:
            name = str(b["measure"]).lower()
            if name not in tt_names.get(bench, ()):
                continue
            nz = near_zero(base.get(name), bound_scale(b))
            verdict[name] = verdict.get(name, True) and nz
    return {n for n, nz in verdict.items() if nz}


def check_sensitivity_baselines(bounds: list[dict], bench: str,
                                sens: dict[str, float],
                                baseline: dict) -> None:
    """Refuse (CheckError) a sensitivity on a measure whose unmutated tt
    value sits near zero: below SPREAD_FLOOR of its own bound's magnitude,
    a relative move is the simulator's absolute tolerance, not the mutant
    (a 1 uV v_low reads as a 100x move on a 1 uV change)."""
    for b in bounds:
        name = str(b["measure"]).lower()
        if name not in sens:
            continue
        scale = bound_scale(b)
        v = baseline.get(name)
        if near_zero(v, scale):
            raise CheckError(
                f"tb/{bench}: {b['measure']!r} declares a sensitivity but "
                f"its unmutated tt value is {v!r}, near zero against its "
                f"bound ({scale:g}) - a relative move there is simulator "
                "tolerance, not a mutant; drop the sensitivity")


def abs_floor(bound: dict, texts: list[str]) -> tuple[float, str]:
    """(floor, what it is) for a bound's `sensitivity_abs`: the smallest
    absolute move, in its `sensitivity_unit`, the deck in `texts` (the bench
    and the netlist) resolves at all. A time ("s") is the transient's
    effective max step, the largest over every tran line - a crossing is
    only placed to within one step. A voltage ("V") is vntol + reltol x
    the bound's magnitude, and a current ("A") abstol + reltol x it -
    ngspice's own convergence test, |change| <= reltol x |value| + vntol
    (abstol), at the scale the spec grades the measure on. Options come
    from the deck's own `.options` lines, ngspice's defaults otherwise.
    CheckError when the deck gives no floor to read."""
    unit, name = bound["sensitivity_unit"], bound["measure"]
    try:
        if unit == "s":
            steps = netlistlib.tran_max_steps(texts)
            if not steps:
                raise ValueError("the bench runs no transient, so no time "
                                 "step bounds the measure")
            return max(steps), f"the transient's max step {max(steps):g} s"
        tol = netlistlib.sim_tolerances(texts)
    except ValueError as exc:
        raise CheckError(
            f"{name!r} declares a sensitivity_abs in {unit!r} but the deck "
            f"gives no simulator floor to hold it to: {exc} - write the "
            "tran line and .options as plain numbers, or drop the "
            "sensitivity_abs") from None
    scale = bound_scale(bound)
    absolute = tol["vntol"] if unit == "V" else tol["abstol"]
    label = "vntol" if unit == "V" else "abstol"
    floor = absolute + tol["reltol"] * scale
    return floor, (f"{label} {absolute:g} + reltol {tol['reltol']:g} x the "
                   f"bound's {scale:g} = {floor:g} {unit}")


def bench_abs_sensitivities(bounds: list[dict], bench: str,
                            tt_measures: set[str],
                            texts: list[str]) -> dict[str, dict]:
    """{measure: {sensitivity, unit, scale}} for every bound declaring a
    `sensitivity_abs`. Refused (CheckError) on a bound not scored at tt,
    and below abs_floor(): a smaller move is the simulator's own
    resolution, not the mutant."""
    out: dict[str, dict] = {}
    for b in bounds:
        if "sensitivity_abs" not in b:
            continue
        name = str(b["measure"]).lower()
        s = float(b["sensitivity_abs"])
        unit = b["sensitivity_unit"]
        if name not in tt_measures:
            raise CheckError(
                f"tb/{bench}: {b['measure']!r} declares a sensitivity_abs "
                "but is not scored at tt - bench_strength runs tt only; drop "
                "it")
        try:
            floor, why = abs_floor(b, texts)
        except CheckError as exc:
            raise CheckError(f"tb/{bench}: {exc}") from None
        # "refuse an absolute sensitivity that sits inside simulator noise"
        if s < floor:
            raise CheckError(
                f"tb/{bench}: {b['measure']!r} sensitivity_abs {s:g} {unit} "
                f"is below the simulator floor, {why} - a smaller move is "
                f"the deck's own resolution, not a mutant; declare at least "
                f"{floor:g} {unit}")
        out[name] = {"sensitivity": s, "unit": unit, "scale": bound_scale(b)}
    return out


def sensitivity_kill(baseline: dict, measures: dict, sens: dict[str, float],
                     abs_sens: dict[str, dict] | None = None) -> dict | None:
    """The largest move past its declared sensitivity, as {measure, delta,
    sensitivity, kind}, or None. kind "relative": delta is (mutant -
    baseline) / baseline, and a measure whose baseline is 0 or missing
    cannot kill this way. kind "absolute" (abs_sens, also naming its unit):
    delta is mutant - baseline, and it kills at |delta| >= sensitivity -
    only on a measure near_zero() against its bound, where the relative
    rule is refused; elsewhere it is a no-op, so it only ever adds kills
    the relative rule cannot make. A measure the mutant never printed
    kills neither way."""
    best = None
    for name in sorted(sens):
        b, v = baseline.get(name), measures.get(name)
        if b is None or v is None or b == 0:
            continue
        d = (v - b) / b
        if abs(d) > sens[name] and (best is None
                                    or abs(d) > abs(best["delta"])):
            best = {"measure": name, "delta": round(d, 6),
                    "sensitivity": sens[name], "kind": "relative"}
    if best is not None:
        return best
    for name in sorted(abs_sens or {}):
        s = abs_sens[name]
        b, v = baseline.get(name), measures.get(name)
        if b is None or v is None or not near_zero(b, s["scale"]):
            continue
        d = v - b
        # "scores as |mutant - baseline| >= it, for the near-zero measures
        # the relative rule refuses"
        if abs(d) >= s["sensitivity"] and (
                best is None or abs(d) / s["sensitivity"]
                > abs(best["delta"]) / best["sensitivity"]):
            best = {"measure": name, "delta": float(f"{d:.6g}"),
                    "sensitivity": s["sensitivity"], "kind": "absolute",
                    "unit": s["unit"]}
    return best


def absolute_deltas(baselines: dict[str, dict], measures: dict[str, dict],
                    names: frozenset[str]) -> dict[str, float]:
    """{measure: mutant - baseline} over `names` (the near-zero measures),
    the larger |delta| where two benches share a name - what a bench-writer
    sizes a sensitivity_abs from."""
    out: dict[str, float] = {}
    for bench, mm in measures.items():
        base = baselines.get(bench) or {}
        for name in sorted(names):
            b, v = base.get(name), mm.get(name)
            if b is None or v is None:
                continue
            d = v - b
            if name not in out or abs(d) > abs(out[name]):
                out[name] = d
    return out


def check_below_spread(ruling: dict, mutant: dict, tt_measures: set[str],
                       source_lines: set[str],
                       near_zero_names: frozenset[str] = frozenset()) -> None:
    """Refuse (CheckError) a below_spread ruling the gate's own run does
    not bear out; return None when it holds. A measure in near_zero_names
    (near_zero_measures()) other than the ruling's own is not held to the
    relative spread limit: its relative move is simulator tolerance. This
    only runs for a survivor, so such a measure crossing a bound has
    already killed the mutant."""
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
        if name == measure or name in near_zero_names:
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


def coverage_gaps(netlist_text: str, declared: list,
                  mutants) -> list[dict]:
    """[{device, kind, reason, declared}] for every device no mutant was
    generated for: each netlist element (netlistlib.parse_elements - every
    element letter, not only `x` lines) and each declared ref that is in
    neither the netlist nor, as a mutable source, any bench. None of them
    is ever in the killed tally, because no mutant of theirs ran."""
    mutated = {str(m["ref"]).lower() for m in mutants}
    want = {str(r).lower() for r in declared}
    out: list[dict] = []
    seen: set[str] = set()
    for el in netlistlib.parse_elements(netlist_text):
        ref = el["ref"].lower()
        if ref in seen:
            continue
        seen.add(ref)
        if ref in mutated:
            continue
        if ref in want:
            reason = netlistlib.unmutable_reason(el, netlist_text)
        else:
            reason = "not in spec.yaml devices"
        out.append({"device": el["ref"], "kind": el["kind"],
                    "reason": reason, "declared": ref in want})
    for r in declared:
        ref = str(r).lower()
        if ref in seen or ref in mutated:
            continue
        seen.add(ref)
        out.append({"device": str(r), "kind": "unknown", "declared": True,
                    "reason": "neither an element of the netlist nor a "
                    "plain `i`/`v` source with a numeric value in any tb/ "
                    "bench"})
    return out


def prepare(ws: Path, timeout: float, out_subdir: str = OUT_SUBDIR) -> dict:
    """Everything a mutant run needs, with every refusal the gate makes
    before its first mutant: each bench's baseline is run and timed, in
    alphabetical order, and its sensitivities checked. `plans` maps each
    mutant id (in the order the benches generate them) to its
    [(bench, mutant)] in `order`: cheapest baseline first, ties by name."""
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

    out_dir = ws / out_subdir
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)

    run_args = {"eda_bin": eda_bin, "ws": ws, "netlist_path": netlist_path,
                "netlist_text": netlist_text, "corner": tt_corner,
                "t_root": t_root, "nominal_vdd": nominal_vdd,
                "out_dir": out_dir, "timeout": timeout, "sizing": sizing}
    ctx: dict[str, dict] = {}         # bench name -> what scoring needs
    baselines: dict[str, dict] = {}   # bench name -> its tt measures
    tt_names: dict[str, set] = {}     # bench name -> measures bound at tt
    bounds_by_bench: dict[str, list] = {}  # bench name -> its bounds
    bench_texts: list[str] = []
    generated: dict[str, list] = {}   # mutant id -> [(bench, mutant)]
    for bench_path, bounds_path in sim_run.find_benches(ws):
        bench_text = bench_path.read_text(encoding="utf-8")
        bench_texts.append(bench_text)
        bounds = simlib.load_bounds(bounds_path)
        bounds_by_bench[bench_path.name] = bounds
        tt_names[bench_path.name] = {
            str(b["measure"]).lower() for b in bounds
            if simlib.scored_at(b, corners_mod.names_of(tt_corner))}
        t0 = time.monotonic()
        baseline = run_mutant(
            eda_bin, ws, {"id": "baseline", "target": None}, netlist_path,
            netlist_text, bench_path, bench_text, bounds, tt_corner, t_root,
            nominal_vdd, out_dir, timeout, sizing)
        seconds = time.monotonic() - t0
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
        baselines[bench_path.name] = baseline.get("measures") or {}
        sens = bench_sensitivities(bounds, bench_path.name,
                                   tt_names[bench_path.name])
        check_sensitivity_baselines(bounds, bench_path.name, sens,
                                    baselines[bench_path.name])
        abs_sens = bench_abs_sensitivities(
            bounds, bench_path.name, tt_names[bench_path.name],
            [bench_text, netlist_text])
        ctx[bench_path.name] = {
            "path": bench_path, "text": bench_text, "bounds": bounds,
            "base_keys": {violation_key(v) for v in baseline["violations"]},
            "baseline": baselines[bench_path.name], "sens": sens,
            "abs_sens": abs_sens, "seconds": seconds}
        for mutant in netlistlib.device_mutants(netlist_text, devices,
                                                bench_text):
            generated.setdefault(mutant["id"], []).append(
                (bench_path.name, mutant))
    # "Order the benches so the cheap ones run first, by the baseline's own
    # measured runtime" - to 0.1 s, so two near-equal benches order by name
    # rather than by timer noise.
    order = sorted(ctx, key=lambda n: (round(ctx[n]["seconds"], 1), n))
    rank = {n: i for i, n in enumerate(order)}
    plans = {mid: sorted(pairs, key=lambda p: rank[p[0]])
             for mid, pairs in generated.items()}
    return {"spec": spec, "devices": devices, "netlist_text": netlist_text,
            "run_args": run_args, "ctx": ctx, "baselines": baselines,
            "tt_names": tt_names, "bounds_by_bench": bounds_by_bench,
            "bench_texts": bench_texts, "order": order, "plans": plans}


def score_all(state: dict, ids: list[str], jobs: int | None) -> dict:
    """{mutant id: score_mutant() entry} for `ids`, in `ids` order, run
    through run_pool(): the same entries whatever `jobs` is."""
    names = sorted(state["ctx"])
    entries = run_pool(
        ids, lambda mid: score_mutant(state["plans"][mid], state["ctx"],
                                      state["run_args"], names), jobs)
    return {e["id"]: e for e in entries}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--jobs", default=os.environ.get(JOBS_ENV, "auto"),
                    help="mutants simulated at once: 'auto' (default; "
                    "max(1, cores - 1-minute load), re-read as it goes) or "
                    f"a number; ${JOBS_ENV} sets the default")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)
    jobs = parse_jobs(args.jobs)

    ws = Path(args.workspace)
    rulings = rulingslib.load(ws, "bench_strength")
    state = prepare(ws, args.timeout)
    spec, devices = state["spec"], state["devices"]
    netlist_text = state["netlist_text"]
    baselines, tt_names = state["baselines"], state["tt_names"]
    bounds_by_bench = state["bounds_by_bench"]
    bench_texts = state["bench_texts"]
    by_id = score_all(state, list(state["plans"]), jobs)

    unmutated = coverage_gaps(netlist_text, devices, by_id.values())
    if not by_id:
        raise CheckError(
            "no device mutants were generated - check spec.yaml 'devices' "
            "against the netlist's own x-line refdes and tb/'s bias sources"
            " (unmutated: " + ", ".join(
                f"{u['device']} ({u['reason']})" for u in unmutated) + ")")
    elements = netlistlib.parse_elements(netlist_text)
    devices_total = len({e["ref"].lower() for e in elements}
                        | {str(r).lower() for r in devices})

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
    near_zero_names = frozenset(
        near_zero_measures(bounds_by_bench, baselines, tt_names))
    for m in survivors:
        m["abs_deltas"] = absolute_deltas(baselines, m["measures"],
                                          near_zero_names)
    for mid, ruling in below.items():
        check_below_spread(ruling, by_id[mid], all_tt,
                           bench_lines if by_id[mid]["target"] == "bench"
                           else netlist_lines, near_zero_names)

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

    for u in unmutated:
        if u["declared"]:
            violations.append(checklib.violation(
                "bench_strength", "error", None, None, "device_not_mutated",
                [u["device"]],
                f"{u['device']} ({u['kind']}) is in spec.yaml devices but no "
                f"mutant was generated for it: {u['reason']} - it is not in "
                "the killed tally, and the gate cannot vouch for the bench "
                "on it", "netlistlib"))
        else:
            violations.append(checklib.violation(
                "bench_strength", "warning", None, None, "device_undeclared",
                [u["device"]],
                f"{u['device']} ({u['kind']}) is in the netlist but not in "
                "spec.yaml devices, so it was never mutated - the kill "
                "tally does not cover it", "netlistlib"))

    def fact(m: dict) -> dict:
        f = {"kind": m["kind"], "killed": m["killed"],
             "describe": m["describe"]}
        if not m["killed"]:
            f["deltas"] = {k: round(v, 6) for k, v in sorted(m["deltas"].items())}
            if m["abs_deltas"]:
                f["abs_deltas"] = {k: float(f"{v:.6g}") for k, v in
                                   sorted(m["abs_deltas"].items())}
        else:
            f["killed_by"] = m["killed_by"]
        if "sensitivity_kill" in m:
            f["sensitivity_kill"] = m["sensitivity_kill"]
        return f

    payload = checklib.report(
        SCRIPT, ws / "netlist", violations, top=spec.get("top"),
        total_mutants=total, killed=total - len(survivors),
        survived=len(survivors),
        devices_total=devices_total,
        devices_mutated=devices_total - len(unmutated),
        unmutated=[{k: u[k] for k in ("device", "kind", "reason")}
                   for u in unmutated],
        killed_by_sensitivity=sum(1 for m in by_id.values()
                                  if "sensitivity_kill" in m),
        killed_by_sensitivity_kind={
            k: sum(1 for m in by_id.values()
                   if m.get("sensitivity_kill", {}).get("kind") == k)
            for k in ("relative", "absolute")},
        equivalent=len(equivalent), below_spread=len(below),
        equivalent_ids=sorted(equivalent), below_spread_ids=sorted(below),
        bench_order=state["order"],
        mutants={mid: fact(m) for mid, m in sorted(by_id.items())})
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
