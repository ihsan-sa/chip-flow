#!/usr/bin/env python
"""sim_run.py - run /ade's ngspice benches at one or more PVT corners (docs/
design.md 1.3, 1.5, "### M8."). Ported from /hwde's scripts/sim_run.py in
BEHAVIOR (bounds-driven scoring, one report per testbench), not mechanism:
engine/lib/simlib.py's own docstring covers the InSpice-shared-library ->
`eda ngspice -b` swap and why nothing here ever trusts ngspice's exit code.

    sim_run.py --workspace DIR [--corners NAME [NAME ...]] [--timeout SEC]
               [--out FILE]

For every `tb/*.cir` with a matching `tb/*.bounds.json` sidecar, at every
requested corner (default: corners.py's own default_corners(), design.md
5's "the four extremes with typical, never fewer"), materializes a runnable
deck - the bench file's own `{{PDK}}`/`{{CORNER}}`/`{{TEMP_C}}`/`{{VDD}}`/
`{{NETLIST}}`/`{{SIZING}}`/`{{RES_CORNER}}`/`{{MIM_CORNER}}` placeholders
filled in (simlib.materialize) -
runs it through `eda ngspice -b`, and scores it against the sidecar
(simlib.compare_bounds; a bound whose optional `corners` list does not
name this corner is skipped there) after checking for a known ngspice failure
signature regardless of exit code (simlib.detect_engine_errors). A bench
over a netlist with a poly resistor or MIM cap that hard-codes the typical
passive section while the sweep moves it is a passive_corner_unselected
error (unselected_passives).

A (bench, corner) pair where every bound in the bench's sidecar has a
`corners` list that leaves this corner out is not run at all (skip_unscored,
the default): nothing would be scored there, and a bench that gates its own
analysis on that list would only make ngspice report an empty run. Each
skipped pair is listed in the result's `not_scored` ({bench, corner,
reason}); a bench with any "all"-scoped bound runs at every corner. A bound
names a corner by any of its names (corners.names_of: a bound scoped
`tt_27c` is scored when the run's corner is `tt`, one scoped `tt_pss` is
not). A bench run at NO requested corner is listed in `out_of_scope` when
some corner of the spec's sweep scores it (sim_tt skips an ff-only bench
that sim_pvt runs - not a failure, not a pass), and is a
`sim_bench_not_run` error when none does, never a silent pass; so is a run
in which no bench ran at all. check_netlist_lint passes skip_unscored=False:
its dry run scores engine errors, not bounds, so it runs every bench.

check_netlist_lint.py, check_sim_tt.py, check_sim_pvt.py and
check_bench_strength.py call run_workspace_benches()/run_bench_at_corner()
directly, IN-PROCESS - the same shape check_sim.py uses for cocotblib
(docs/design.md 1.2: no bin/eda subprocess where none is needed) - never a
subprocess of this script. The CLI below is for standalone smoke use and
parity with /hwde's own sim_run.py entry point.

Timeout: each bench run gets the caller's --timeout (default 60 s), or
longer when the bench asks for it with a comment line in its tb/*.cir -
`* sim_timeout_s: 600` - which the bench-writer sets for a long sweep (a
256-code DAC bench). The larger of the two wins, capped at
MAX_BENCH_TIMEOUT. A run that times out is a `sim_engine_error_sim_timeout`
finding, never a pass.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import corners as corners_mod  # noqa: E402
import simlib  # noqa: E402
import speclib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "sim_run"
EDA_BIN = REPO / "bin" / "eda"
DEFAULT_TIMEOUT = 60.0
MAX_BENCH_TIMEOUT = 3600.0
BENCH_TIMEOUT_RE = re.compile(r"^\*\s*sim_timeout_s\s*[:=]\s*(\S+)", re.M | re.I)
PDK_REL = Path("foss") / "pdks" / "gf180mcuD"


def toolchain_root(eda_bin: Path | None = None, timeout: float = 30.0) -> Path:
    # eda_bin's default is resolved HERE, not in the signature: a default
    # argument value is bound once, at function-definition time, so a
    # caller (or a test) that later points sim_run.EDA_BIN somewhere else
    # would otherwise never be honored by a bare toolchain_root() call.
    eda_bin = eda_bin or EDA_BIN
    proc = subprocess.run([str(eda_bin), "--print-toolchain-root"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError("could not resolve the eda toolchain root: "
                         f"{(proc.stderr or proc.stdout).strip()}")
    return Path(proc.stdout.strip())


def pdk_root(t_root: Path) -> Path:
    return t_root / PDK_REL


def find_netlist(ws: Path) -> Path:
    files = sorted((ws / "netlist").glob("*.cir"))
    if not files:
        raise CheckError(f"no netlist/*.cir under {ws / 'netlist'}")
    return files[0]


def find_benches(ws: Path) -> list[tuple[Path, Path]]:
    """[(cir_path, bounds_path), ...] for every `tb/*.cir` with a matching
    `tb/<stem>.bounds.json` sidecar. A `.cir` with no sidecar is silently
    skipped (a scratch deck a caller wrote elsewhere for its own purposes
    never lands under tb/ in the first place)."""
    tb = ws / "tb"
    out = []
    for cir in sorted(tb.glob("*.cir")):
        bounds = tb / f"{cir.stem}.bounds.json"
        if bounds.is_file():
            out.append((cir, bounds))
    if not out:
        raise CheckError(f"no tb/*.cir with a matching *.bounds.json sidecar "
                         f"under {tb}")
    return out


def load_sizing(ws: Path) -> dict:
    """sizing/sizing.yaml (design.md 4: `{name: {value, min?, max?}}`, the
    optimise target) - {} when the block has none (most benches don't)."""
    p = ws / "sizing" / "sizing.yaml"
    if not p.is_file():
        return {}
    import yaml
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise CheckError(f"{p} must be a YAML mapping of {{name: value-or-"
                         "{value,min,max}}}")
    return data


# The PDK's own sm141064.spice keeps a passive device's sheet resistance in
# a SEPARATE `.lib res_<corner> ... .endl` block from the transistor
# `.lib <corner> ... .endl` block {{CORNER}} already selects (confirmed by
# reading the real file while wiring up corpus/ade/r2r_dac's bench: `rm1`'s
# own default r_rsh0=rsh_rm1 is undefined - a real "Formula() error" from
# ngspice, not a guess - unless one of these is also entered), and a MIM
# cap's in `.lib mimcap_<corner>`. {{RES_CORNER}} and {{MIM_CORNER}} pick
# them from corners.passive_of(corner): the corner's own `passive` key (a
# passive corner), else its process where the PDK has a passive section
# (typical/ss/ff), else typical (sf/fs have none) - and typical at every
# corner for a device the person scoped out at H1 (the corner's `pinned`
# key; corners.py, "Scoped-out dimensions").
PASSIVE_PLACEHOLDERS = {"resistor": "RES_CORNER", "mim_cap": "MIM_CORNER"}


def unselected_passives(bench_text: str, passives: list[str],
                        corner_list: list[dict]) -> list[str]:
    """The placeholders a bench must use and does not: when the sweep
    moves a passive off typical, a bench over a netlist with that passive
    that hard-codes `res_typical`/`mimcap_typical` instead of
    {{RES_CORNER}}/{{MIM_CORNER}} would simulate every passive corner at
    typical and pass them all unseen. A device no corner moves (pinned
    by a recorded scope-out, or a sweep that never leaves typical) owes
    no placeholder. Pure."""
    return [PASSIVE_PLACEHOLDERS[d] for d in passives
            if any(corners_mod.passive_of(c, d) != "typical"
                   for c in corner_list)
            and "{{" + PASSIVE_PLACEHOLDERS[d] + "}}" not in bench_text]


def build_subs(t_root: Path, netlist_path: Path, corner: dict,
              nominal_vdd: float, sizing: dict) -> dict:
    return {
        "PDK": str(pdk_root(t_root)),
        # absolute: ngspice runs with cwd=log/sim, so a netlist path taken
        # from a relative --workspace would not resolve there
        "NETLIST": str(Path(netlist_path).resolve()),
        "CORNER": corner["process"],
        "RES_CORNER": f"res_{corners_mod.passive_of(corner, 'resistor')}",
        "MIM_CORNER": f"mimcap_{corners_mod.passive_of(corner, 'mim_cap')}",
        "TEMP_C": corner["temp_c"],
        "VDD": f"{corners_mod.resolve_vdd(corner, nominal_vdd):.6g}",
        "SIZING": simlib.sizing_param_line(sizing),
    }


def bench_timeout(bench_name: str, bench_text: str, timeout: float) -> float:
    """The caller's timeout, raised to the bench's own `* sim_timeout_s: N`
    line when it has one. A value that is not a number in
    (0, MAX_BENCH_TIMEOUT] is a CheckError, not silently ignored."""
    m = BENCH_TIMEOUT_RE.search(bench_text)
    if not m:
        return timeout
    try:
        want = float(m.group(1))
    except ValueError:
        want = float("nan")
    if not 0 < want <= MAX_BENCH_TIMEOUT:
        raise CheckError(f"{bench_name}: 'sim_timeout_s: {m.group(1)}' must "
                         f"be a number of seconds in (0, {MAX_BENCH_TIMEOUT:g}]")
    return max(timeout, want)


def run_bench_at_corner(eda_bin: Path, bench_name: str,
                        bench_template_text: str, bounds: list[dict],
                        subs: dict, corner: dict, out_dir: Path,
                        timeout: float, check: str = "sim") -> dict:
    """Materialize `bench_template_text` with `subs`, run it, score it.
    `bench_name` is what violations/report entries call this bench (the
    real tb/ filename even when the text passed in is a mutant's - see
    check_bench_strength.py, which never writes a mutated bench to disk
    under tb/ itself)."""
    text = simlib.materialize(bench_template_text, subs)
    out_dir.mkdir(parents=True, exist_ok=True)
    deck_path = out_dir / f"{Path(bench_name).stem}__{corner['name']}.cir"
    deck_path.write_text(text, encoding="utf-8")
    timeout = bench_timeout(bench_name, bench_template_text, timeout)
    stdout, stderr, rc = simlib.run_ngspice(eda_bin, deck_path, out_dir, timeout)
    measures = simlib.parse_measures(stdout)
    failed = simlib.parse_failed_measures(stderr)
    err_kinds = simlib.detect_engine_errors(stdout + "\n" + stderr)

    violations: list[dict] = []
    if err_kinds:
        tail_lines = [ln for ln in (stderr or stdout).strip().splitlines() if ln.strip()]
        detail = tail_lines[-1][:300] if tail_lines else "(no output)"
        violations += simlib.engine_error_violations(
            check, bench_name, corner["name"], err_kinds, detail)
    names = corners_mod.names_of(corner)
    violations += simlib.compare_bounds(
        bounds, measures, bench_name, corner=corner["name"],
        failed_measures=failed, check=check,
        unsettled=simlib.parse_unsettled(stdout), names=names)

    out = {
        "bench": bench_name, "corner": corner["name"], "names": sorted(names),
        "process": corner["process"],
        "passive": corners_mod.passive_of(corner),
        "temp_c": corner["temp_c"], "vdd": subs["VDD"], "returncode": rc,
        "measures": measures, "engine_errors": err_kinds,
        "violations": violations, "deck": str(deck_path),
    }
    if corner.get("pinned"):
        out["pinned"] = dict(corner["pinned"])
    return out


def spec_sweep(ws: Path) -> tuple[list[dict], list[dict]]:
    """The corner set the spec asks sim_pvt to sweep - spec.yaml's
    `corners` through corners.spec_corners, with the passive corners the
    netlist brings in and the person's recorded H1 scope-outs pinned -
    and those scope-outs. check_sim_pvt runs it; check_sim_tt asks it
    which benches another corner scores."""
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    passives = corners_mod.passive_devices(find_netlist(ws).read_text(
        encoding="utf-8", errors="replace"))
    state_path = ws / "state.json"
    scoped_out = corners_mod.recorded_scope_outs(
        checklib.load_json(state_path, "state.json")
        if state_path.is_file() else {})
    sweep = corners_mod.spec_corners(
        corners_mod.load(), spec.get("corners", "default"), passives,
        pinned=[s["dimension"] for s in scoped_out])
    return sweep, scoped_out


def run_workspace_benches(ws: Path, eda_bin: Path | None = None,
                          corner_names: list[str] | None = None,
                          corners: list[dict] | None = None,
                          timeout: float = DEFAULT_TIMEOUT,
                          check: str = "sim",
                          out_subdir: str = "log/sim",
                          skip_unscored: bool = True,
                          scope_corners: list[dict] | None = None) -> dict:
    """Run every tb/*.cir with a bounds sidecar at every requested corner
    (default: corners.py's default_corners()) - `corners` passes the corner
    dicts themselves (a spec's grid has names corners.yaml never lists),
    `corner_names` picks from default_corners by name. With skip_unscored, a
    corner no bound of the bench is scored at (by any of its names,
    corners.names_of) is skipped and listed in `not_scored`. A bench skipped
    at every requested corner is listed in `out_of_scope` when some corner
    of `scope_corners` (the spec's whole sweep, spec_sweep(); default: the
    requested corners) scores it - another gate's run scores it there - and
    is a sim_bench_not_run error when none does. No bench run at all is a
    sim_bench_not_run error too: the gate scored nothing. Returns {top,
    corners, results: [run_bench_at_corner() dicts], not_scored: [{bench,
    corner, reason}], out_of_scope: [{bench, scored_at}], violations:
    [flattened]}."""
    eda_bin = eda_bin or EDA_BIN  # resolved here, not as a stale-bound
    # default value - see toolchain_root()'s own comment on why.
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    supply = spec.get("supply") or {}
    nominal_vdd = supply.get("vdd")
    if isinstance(nominal_vdd, bool) or not isinstance(nominal_vdd, (int, float)):
        raise CheckError("spec.yaml has no numeric 'supply.vdd' - run "
                         "spec_lint first")

    t_root = toolchain_root(eda_bin)
    netlist_path = find_netlist(ws)
    sizing = load_sizing(ws)
    corners_data = corners_mod.load()
    if corners:
        corner_list = [dict(c) for c in corners]
    elif corner_names:
        corner_list = corners_mod.corners_by_name(corners_data, corner_names)
    else:
        corner_list = corners_mod.default_corners(corners_data)

    out_dir = ws / out_subdir
    shutil.rmtree(out_dir, ignore_errors=True)
    passives = corners_mod.passive_devices(
        netlist_path.read_text(encoding="utf-8", errors="replace"))

    results = []
    unselected = []
    not_scored = []
    out_of_scope = []
    not_run = []
    for bench_path, bounds_path in find_benches(ws):
        bounds = simlib.load_bounds(bounds_path)
        template_text = bench_path.read_text(encoding="utf-8")
        for token in unselected_passives(template_text, passives, corner_list):
            unselected.append(checklib.violation(
                check, "error", f"tb/{bench_path.name}", None,
                "passive_corner_unselected", [token],
                f"{bench_path.name}: the netlist uses a device whose spread "
                f"lives in the PDK's passive sections, and the sweep moves it "
                f"off typical, but the bench never uses {{{{{token}}}}} - "
                f"every passive corner would simulate at typical. Replace "
                f"the bench's hard-coded `.lib ... res_typical` / "
                f"`mimcap_typical` line with `.lib ... {{{{{token}}}}}` - "
                f"unless the person ruled that spread out of scope at H1, "
                f"which is recorded from their answer with state.py "
                f"scope-out, never by editing the bench",
                "sim_run"))
        ran = 0
        for corner in corner_list:
            # Brief: "don't run a (bench, corner) pair when every bound in
            # that bench's sidecar has a corners list that excludes the
            # corner" - so it runs when ANY bound is scored here.
            names = corners_mod.names_of(corner)
            if skip_unscored and not any(simlib.scored_at(b, names)
                                         for b in bounds):
                not_scored.append({
                    "bench": bench_path.name, "corner": corner["name"],
                    "reason": "no bound in the sidecar is scored at this "
                              "corner"})
                continue
            subs = build_subs(t_root, netlist_path, corner, nominal_vdd, sizing)
            results.append(run_bench_at_corner(
                eda_bin, bench_path.name, template_text, bounds, subs,
                corner, out_dir, timeout, check=check))
            ran += 1
        # Policy (planning seat): a bench whose bounds are all scoped away
        # from the corner being run "is skipped by sim_tt and listed in the
        # report as out of scope for that corner ... This only holds when
        # some corner of the spec's grid scores it ... If no corner in the
        # grid scores a bench's bounds, refuse."
        if ran:
            continue
        sweep = scope_corners or corner_list
        reach = [c["name"] for c in sweep
                 if any(simlib.scored_at(b, corners_mod.names_of(c))
                        for b in bounds)]
        if reach:
            out_of_scope.append({"bench": bench_path.name, "scored_at": reach})
        else:
            # a gate that did not run is a refusal, never a pass
            not_run.append(checklib.violation(
                check, "error", f"tb/{bench_path.name}", None,
                "sim_bench_not_run", [bench_path.name],
                f"{bench_path.name}: no bound in its sidecar is scored at any "
                f"of the spec's corners "
                f"({', '.join(c['name'] for c in sweep)}), so no gate ever "
                f"runs it. Scope at least one bound's `corners` to a "
                f"corner in this sweep (or \"all\"), or name the corner in the "
                f"spec's corner set",
                "sim_run"))
    if not results and not not_run:
        # every bench is out of scope here: the gate scored nothing
        not_run.append(checklib.violation(
            check, "error", "tb/", None, "sim_bench_not_run", [],
            f"no bench has a bound scored at any of the corners this run "
            f"sweeps ({', '.join(c['name'] for c in corner_list)}), so the "
            f"gate scored nothing. Scope at least one bound to one of them "
            f"(or \"all\")", "sim_run"))

    violations = (unselected + not_run
                  + [v for r in results for v in r["violations"]])
    return {"top": spec.get("top"), "corners": [c["name"] for c in corner_list],
           "results": results, "not_scored": not_scored,
           "out_of_scope": out_of_scope, "violations": violations}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--corners", nargs="*", help="corner names (default: "
                    "the full default_corners sweep)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--check", default="sim", help="the check name violations "
                    "are attributed to (default 'sim')")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    # EDA_BIN referenced here (a module-global lookup at call time, not a
    # default-argument value bound once at import) so a caller/test that
    # monkeypatches sim_run.EDA_BIN is honored even through main()/run().
    result = run_workspace_benches(ws, eda_bin=EDA_BIN, corner_names=args.corners,
                                   timeout=args.timeout, check=args.check)
    payload = checklib.report(SCRIPT, ws, result["violations"],
                              top=result["top"], corners=result["corners"],
                              results=result["results"],
                              not_scored=result["not_scored"],
                              out_of_scope=result["out_of_scope"])
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
