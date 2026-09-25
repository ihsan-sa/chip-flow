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

When `mc.enabled: true`: this box's own gf180mcuD design.spice does NOT
default both switches on the way the PDK's own comment block claims ("
(default) - sw_stat_global=1 and sw_stat_mismatch=1") - the file itself
then sets `.param sw_stat_global=0` / `.param sw_stat_mismatch=0` right
below that comment, and pins no `.option seed` at all. Left alone, every
`mc.runs` re-run of the same 'tt'-corner bench is the SAME deterministic
operating point repeated - zero mismatch, zero seed variation, one sample
wearing a yield percentage. So this gate injects `.param sw_stat_mismatch=1`
(and, when spec.yaml's `mc.global` asks for it, `sw_stat_global=1` too) plus
a per-run `.option seed=<mc.seed base + i>` into the GENERATED bench deck -
right after that bench's own `.include '.../design.spice'` (or design.ngspice) line, so the
override lands AFTER the PDK's own zeros are parsed - never by editing the
PDK's own read-only design.spice. Each of the `mc.runs` independent ngspice
invocations then draws a fresh, seeded sample of the PDK's own
agauss()-driven per-instance mismatch parameters (mis_vth, mis_k, mis_r,
...), and yield = the fraction with zero bound violations, scored against
`mc.yield_min`.

Two refusals a caller must not be able to talk this gate out of: `mc.runs:
0` is refused outright (CheckError, exit 2), never silently widened back up
to DEFAULT_RUNS - an explicit "run zero samples" is not the same request as
"no runs field at all"; and every sample producing BIT-IDENTICAL measures
(mismatch/seed injection above did nothing - a broken bench template, a
regressed PDK path, or this script's own regex silently matching zero
times) is refused too, rather than reported as a suspiciously-perfect
yield. `hits == 0` (every sample failed) is also always a violation,
regardless of how low `mc.yield_min` is set - a spec that nominally
tolerates a low yield still never tolerates ALL samples failing.
"""
from __future__ import annotations

import argparse
import re
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
from checklib import CheckError  # noqa: E402

SCRIPT = "check_mc"
DEFAULT_TIMEOUT = 60.0
DEFAULT_RUNS = 20
DEFAULT_SEED_BASE = 1
OUT_SUBDIR = "log/mc"

# The bench's own `.include '.../design.spice'` line - matched BEFORE
# simlib.materialize() runs (so `{{PDK}}` is still a literal placeholder
# here; the regex only needs ".include" and "design.spice" on the same
# line, not a resolved path). The PDK ships the same file twice, as
# design.spice and design.ngspice (byte-identical, both setting the two
# sw_stat switches to 0), and a bench may include either.
_INCLUDE_DESIGN_RE = re.compile(
    r"^(\.include\s+.*design\.(?:ng)?spice.*)$", re.IGNORECASE | re.MULTILINE)


def inject_mc_params(template_text: str, seed: int, do_global: bool) -> str:
    """Insert `.param sw_stat_mismatch=1` (+ `sw_stat_global=1` when
    `do_global`) and `.option seed=<seed>` right after the bench's own
    design.spice include line - overriding that file's own
    sw_stat_global=0/sw_stat_mismatch=0 defaults in the MATERIALIZED DECK
    only; the PDK's own design.spice is never written to."""
    lines = []
    if do_global:
        lines.append(".param sw_stat_global=1")
    lines.append(".param sw_stat_mismatch=1")
    lines.append(f".option seed={seed}")
    injected = "\n".join(lines)
    new_text, n = _INCLUDE_DESIGN_RE.subn(
        lambda m: m.group(1) + "\n" + injected, template_text, count=1)
    if n == 0:
        raise CheckError(
            "mc: bench template has no '.include ...design.spice' (or "
            "design.ngspice) line to "
            "inject sw_stat_mismatch/seed after - cannot run a real Monte "
            "Carlo sample")
    return new_text


def mc_applicable(spec: dict) -> bool:
    """Does this spec ask for Monte Carlo? The ONE predicate for that
    question - this gate's own not-applicable branch below and attest.py's
    release-time declaration (attest.not_applicable_reason) both call it,
    so the two can never disagree about what "not applicable" means. A
    non-mapping `mc` is refused (exit 2), never read as "no MC"."""
    mc_cfg = spec.get("mc")
    if mc_cfg is None:
        return False
    if not isinstance(mc_cfg, dict):
        raise CheckError(
            f"spec.yaml's mc must be a mapping (enabled/runs/yield_min/...), "
            f"got {type(mc_cfg).__name__} - write 'mc: {{enabled: true}}'")
    return bool(mc_cfg.get("enabled"))


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = spec.get("top")

    # Not applicable: a real answer, but NOT a recordable pass in a
    # workspace. It is stamped with spec.yaml, while invalidation.yaml keys
    # mc on [netlist, tb], so state.record_gate refuses it (input_digest
    # mismatch) - deliberately: a recorded "pass" keyed on netlist/tb would
    # stay fresh after spec.yaml later turned MC on, and release would
    # accept a Monte Carlo that never ran. Release instead re-asks the
    # current spec.yaml every time (attest.not_applicable_reason).
    if not mc_applicable(spec):
        payload = checklib.report(SCRIPT, ws / "spec" / "spec.yaml", [],
                                  top=top, applicable=False,
                                  reason="spec.yaml has no 'mc.enabled: true'")
        return payload, args.out

    mc_cfg = spec["mc"]
    if "runs" in mc_cfg:
        runs_val = mc_cfg["runs"]
        if isinstance(runs_val, bool) or not isinstance(runs_val, int) \
                or runs_val <= 0:
            raise CheckError(
                f"mc.runs must be a positive integer, got {runs_val!r} - "
                "'runs: 0' (or negative) is refused outright, never "
                f"silently widened to the default {DEFAULT_RUNS}")
        runs = runs_val
    else:
        runs = DEFAULT_RUNS
    yield_min = float(mc_cfg.get("yield_min", 1.0))
    do_global = bool(mc_cfg.get("global", False))
    seed_base = int(mc_cfg.get("seed", DEFAULT_SEED_BASE))
    seeds = [seed_base + i for i in range(runs)]
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
    measures_by_bench: dict[str, list[dict]] = {}
    for bench_path, bounds_path in sim_run.find_benches(ws):
        bounds = simlib.load_bounds(bounds_path)
        template_text = bench_path.read_text(encoding="utf-8")
        per_bench_measures = measures_by_bench.setdefault(bench_path.name, [])
        for i, seed in enumerate(seeds):
            mc_text = inject_mc_params(template_text, seed, do_global)
            bench_name = f"{bench_path.stem}__mc{i}"
            result = sim_run.run_bench_at_corner(
                eda_bin, bench_name, mc_text, bounds, subs, tt_corner,
                out_dir, args.timeout, check="mc")
            passed = not result["violations"]
            outcomes.append({"bench": bench_path.name, "run": i,
                            "seed": seed, "passed": passed,
                            "measures": result["measures"]})
            per_bench_measures.append(result["measures"])
            if not passed:
                all_violations.extend(result["violations"])

    for bench_name, ms in measures_by_bench.items():
        if len(ms) > 1 and all(m == ms[0] for m in ms):
            raise CheckError(
                f"mc: all {len(ms)} runs of {bench_name!r} produced "
                f"bit-identical measures {ms[0]!r} across seeds {seeds} - "
                "sw_stat_mismatch/seed injection did not vary the result; "
                "Monte Carlo must never be one sample repeated")

    total = len(outcomes)
    hits = sum(1 for o in outcomes if o["passed"])
    yield_frac = hits / total if total else 0.0
    violations = []
    if hits == 0:
        violations.append(checklib.violation(
            "mc", "error", None, None, "yield_all_failed", [],
            f"every Monte Carlo sample failed (0/{total}) across seeds "
            f"{seeds} - regardless of yield_min", "sim_run"))
    elif yield_frac < yield_min:
        violations.append(checklib.violation(
            "mc", "error", None, None, "yield_below_spec", [],
            f"Monte Carlo yield {yield_frac:.2%} ({hits}/{total}) is below "
            f"the spec's {yield_min:.2%}", "sim_run"))

    payload = checklib.report(
        SCRIPT, ws / "netlist", violations, top=top, applicable=True,
        runs=total, hits=hits, yield_frac=round(yield_frac, 4),
        yield_min=yield_min, seeds=seeds, results=outcomes,
        sample_violations=all_violations[:20])
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
