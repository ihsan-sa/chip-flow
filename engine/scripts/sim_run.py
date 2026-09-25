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
`{{NETLIST}}`/`{{SIZING}}` placeholders filled in (simlib.materialize) -
runs it through `eda ngspice -b`, and scores it against the sidecar
(simlib.compare_bounds) after checking for a known ngspice failure
signature regardless of exit code (simlib.detect_engine_errors).

check_netlist_lint.py, check_sim_tt.py, check_sim_pvt.py and
check_bench_strength.py call run_workspace_benches()/run_bench_at_corner()
directly, IN-PROCESS - the same shape check_sim.py uses for cocotblib
(docs/design.md 1.2: no bin/eda subprocess where none is needed) - never a
subprocess of this script. The CLI below is for standalone smoke use and
parity with /hwde's own sim_run.py entry point.
"""
from __future__ import annotations

import argparse
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
# ngspice, not a guess - unless one of these is also entered). Only
# typical/ss/ff have their own resistor corner section; sf/fs (mixed
# transistor-threshold corners) have no resistor-side equivalent, so a
# resistor-only bench at either falls back to res_typical - there is no
# other sane choice the PDK itself offers.
RES_LIB_SECTION = {"typical": "res_typical", "ss": "res_ss", "ff": "res_ff",
                   "sf": "res_typical", "fs": "res_typical"}


def build_subs(t_root: Path, netlist_path: Path, corner: dict,
              nominal_vdd: float, sizing: dict) -> dict:
    return {
        "PDK": str(pdk_root(t_root)),
        # absolute: ngspice runs with cwd=log/sim, so a netlist path taken
        # from a relative --workspace would not resolve there
        "NETLIST": str(Path(netlist_path).resolve()),
        "CORNER": corner["process"],
        "RES_CORNER": RES_LIB_SECTION.get(corner["process"], "res_typical"),
        "TEMP_C": corner["temp_c"],
        "VDD": f"{corners_mod.resolve_vdd(corner, nominal_vdd):.6g}",
        "SIZING": simlib.sizing_param_line(sizing),
    }


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
    violations += simlib.compare_bounds(
        bounds, measures, bench_name, corner=corner["name"],
        failed_measures=failed, check=check)

    return {
        "bench": bench_name, "corner": corner["name"], "process": corner["process"],
        "temp_c": corner["temp_c"], "vdd": subs["VDD"], "returncode": rc,
        "measures": measures, "engine_errors": err_kinds,
        "violations": violations, "deck": str(deck_path),
    }


def run_workspace_benches(ws: Path, eda_bin: Path | None = None,
                          corner_names: list[str] | None = None,
                          timeout: float = DEFAULT_TIMEOUT,
                          check: str = "sim",
                          out_subdir: str = "log/sim") -> dict:
    """Run every tb/*.cir with a bounds sidecar at every requested corner
    (default: corners.py's default_corners()). Returns {top, corners,
    results: [run_bench_at_corner() dicts], violations: [flattened]}."""
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
    corner_list = (corners_mod.corners_by_name(corners_data, corner_names)
                  if corner_names else corners_mod.default_corners(corners_data))

    out_dir = ws / out_subdir
    shutil.rmtree(out_dir, ignore_errors=True)

    results = []
    for bench_path, bounds_path in find_benches(ws):
        bounds = simlib.load_bounds(bounds_path)
        template_text = bench_path.read_text(encoding="utf-8")
        for corner in corner_list:
            subs = build_subs(t_root, netlist_path, corner, nominal_vdd, sizing)
            results.append(run_bench_at_corner(
                eda_bin, bench_path.name, template_text, bounds, subs,
                corner, out_dir, timeout, check=check))

    violations = [v for r in results for v in r["violations"]]
    return {"top": spec.get("top"), "corners": [c["name"] for c in corner_list],
           "results": results, "violations": violations}


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
                              results=result["results"])
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
