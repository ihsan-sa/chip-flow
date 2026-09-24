#!/usr/bin/env python
"""check_pex_sim.py - the ade `pex_sim` gate (docs/design.md 1.5, 5,
"### M9.").

    check_pex_sim.py --workspace DIR [--block NAME] [--out FILE]

Regenerates the GDS fresh (layout_gen.build(), same discipline as
check_analog_drc.py/check_analog_lvs.py), extracts it with magic INCLUDING
parasitics (extract + ext2sim labels + extresist all, then
`ext2spice ... extresist on` - docs/design.md 1.5: "magic ext2spice with
parasitics"; klayout_pex as the second extractor is not wired up at M9),
then runs the block's own `tb/<block>_pex_tb.cir` template (or
`layout_ref/<block>_pex_tb.cir`, M9's own stand-in while M8's tb/ does not
exist yet in this worktree - same fallback order as check_analog_lvs.py's
reference netlist) at typical corner against the extracted netlist, and
checks every `.control ... print ...` value the bench prints against
`<same-stem>.bounds.json`'s `measures` map ({name: {min, max}}).

The bench template carries two substitution tokens, `{pdk}` (the resolved
toolchain's PDK root) and `{extracted}` (the parasitic-aware netlist this
run just produced) - never hand-written absolute paths, so the same
template runs unchanged on any host/toolchain path.

"the worst corner" (docs/design.md 1.5) is not run here - M9's own
boundary is proving the extract-then-resim path on typical; the full
corner sweep is `/ade`'s `sim_pvt`-style job, out of scope until the skill
session lands.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import layoutlib  # noqa: E402
import layout_gen  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_pex_sim"


def find_bench(ws: Path, block: str) -> tuple[Path, Path]:
    for base in (ws / "tb", ws / "layout_ref"):
        cir = base / f"{block}_pex_tb.cir"
        bounds = base / f"{block}_pex_tb.bounds.json"
        if cir.is_file() and bounds.is_file():
            return cir, bounds
    raise CheckError(
        f"no pex_sim bench for {block!r}: looked for "
        f"tb/{block}_pex_tb.cir (M8's bench, once merged) and "
        f"layout_ref/{block}_pex_tb.cir (M9's own stand-in), each with a "
        "matching .bounds.json - a gate with nothing to run is a refusal, "
        "never a pass")


def run_pex_extract(work_dir: Path, gds_path: Path, topcell: str) -> Path:
    tcl = "\n".join([
        f"gds read {gds_path}",
        f"load {topcell}",
        "select top cell",
        "extract all",
        "ext2sim labels",
        "extresist tolerance 10",
        "extresist all",
        "ext2spice cthresh 0",
        "ext2spice rthresh 0",
        "ext2spice extresist on",
        "ext2spice lvs",
        "ext2spice",
        "quit -noprompt",
    ]) + "\n"
    proc = layoutlib.run_eda(["magic", "-noconsole", "-dnull"], cwd=work_dir,
                             timeout=180.0, stdin_text=tcl)
    out = (proc.stdout or "") + (proc.stderr or "")
    out_path = work_dir / f"{topcell}.spice"
    if not out_path.is_file():
        raise CheckError(
            f"magic parasitic extraction produced no {out_path.name} - the "
            f"run did not complete: {out[-2000:]}")
    return out_path


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--block", help="override state.json's 'block'")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    gds_path, topcell, _abs_path, _abstract = layout_gen.build(ws, args.block)
    block = args.block or topcell

    bench_tpl, bounds_path = find_bench(ws, block)
    bounds = checklib.load_json(bounds_path, "pex_sim bounds")
    measures = bounds.get("measures")
    if not isinstance(measures, dict) or not measures:
        raise CheckError(f"{bounds_path} has no non-empty 'measures' map")

    work_dir = ws / "log" / "pex_sim"
    work_dir.mkdir(parents=True, exist_ok=True)
    extracted = run_pex_extract(work_dir, gds_path.resolve(), topcell)

    pdk = layoutlib.pdk_root()
    bench_text = bench_tpl.read_text(encoding="utf-8").format(
        pdk=pdk, extracted=extracted)
    bench_path = work_dir / f"{block}_pex_tb.cir"
    bench_path.write_text(bench_text, encoding="utf-8")

    sim_out = layoutlib.run_ngspice(bench_path, cwd=work_dir, timeout=120.0)
    values = layoutlib.parse_ngspice_prints(sim_out)
    if not values:
        raise CheckError(
            "ngspice produced no parseable 'name = value' print lines - "
            f"the bench did not measure anything: {sim_out[-2000:]}")

    violations = []
    measured = {}
    for name, bound in measures.items():
        key = name.lower()
        if key not in values:
            violations.append(checklib.violation(
                "pex_sim", "error", str(bounds_path.relative_to(ws)),
                topcell, "measure_missing", [name],
                f"bench never printed a value for {name!r} "
                f"(printed: {sorted(values)})", "ngspice"))
            continue
        v = values[key]
        measured[name] = v
        lo, hi = bound.get("min"), bound.get("max")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            violations.append(checklib.violation(
                "pex_sim", "error", str(bounds_path.relative_to(ws)),
                topcell, "measure_out_of_bounds", [name],
                f"{name} = {v:.6g}, outside bound [{lo}, {hi}]", "ngspice"))

    payload = checklib.report(
        SCRIPT, ws / "layout", violations, topcell=topcell,
        gds=str(gds_path.relative_to(ws)), extracted=str(extracted),
        measured=measured)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
