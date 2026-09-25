#!/usr/bin/env python
"""check_pex_sim.py - the ade `pex_sim` gate (docs/design.md 1.5, 5,
"### M9.").

    check_pex_sim.py --workspace DIR [--block NAME] [--out FILE]

Regenerates the GDS fresh (layout_gen.build(), same discipline as
check_analog_drc.py/check_analog_lvs.py), extracts it with magic including
parasitics - every capacitor down to 0 fF, and the wire resistance of
every net that reaches a transistor (layoutlib.run_magic_extract) - and
refuses a netlist that came back with no R and no C line at all, since a
"post-layout" run on that is the schematic again. klayout_pex as the second
extractor is not wired up at M9.

Then it runs the block's post-layout bench, `tb/<block>_pex_tb.cir` or
`layout_ref/<block>_pex_tb.cir`, at the typical corner against the
extracted netlist, and checks every value the bench prints (`print` or
`meas` in its .control block, "name = value") against the same-stem
`.bounds.json`'s `measures` map ({name: {min, max}}). A bench here should
measure what parasitics move - a pole, a delay, a settling time - not only
a DC point that a wire's capacitance cannot touch.

The bench template carries two substitution tokens, `{pdk}` (the resolved
toolchain's PDK root) and `{extracted}` (the parasitic netlist this run
just produced), so it runs unchanged on any host. The bench instantiates
the extracted cell positionally in the pin order of M8's netlist, and magic
numbers ports in an order of its own, so the extracted `.subckt` line is
rewritten into the reference's pin order first; a pin set that differs is
a refusal.

"the worst corner" (docs/design.md 1.5) is not run here - M9's own
boundary is proving the extract-then-resim path on typical; the full
corner sweep is `/ade`'s `sim_pvt`-style job, out of scope until the skill
session lands.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import layoutlib  # noqa: E402
import layout_gen  # noqa: E402
import netlistlib  # noqa: E402
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
        f"tb/{block}_pex_tb.cir and layout_ref/{block}_pex_tb.cir, each "
        "with a matching .bounds.json - a gate with nothing to run is a "
        "refusal, never a pass")


def pins_of(netlist_text: str, cell: str) -> list[str] | None:
    pins = netlistlib.subckt_pins(netlist_text).get(cell.lower())
    if pins is None:
        return None
    return [p for p in pins if "=" not in p]


def reorder_pins(extracted_text: str, cell: str, want: list[str]) -> str:
    """The extracted netlist with `cell`'s .subckt pins put in `want`'s
    order. Only the header moves: a pin is a node name inside the subckt,
    so its position there changes nothing but how an instance binds."""
    have = pins_of(extracted_text, cell)
    if have is None:
        raise CheckError(f"the extracted netlist declares no .subckt {cell}")
    if sorted(p.lower() for p in have) != sorted(p.lower() for p in want):
        raise CheckError(
            f"the extracted {cell} has pins {have}, the reference has "
            f"{want} - a bench bound to the reference's pins would wire "
            "the layout wrong")
    by_lower = {p.lower(): p for p in have}
    head = re.compile(rf"^(\s*\.subckt\s+{re.escape(cell)})\s.*$",
                      re.IGNORECASE | re.MULTILINE)
    return head.sub(lambda m: m.group(1) + " " + " ".join(
        by_lower[p.lower()] for p in want), extracted_text, count=1)


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--block", help="override state.json's 'block'")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace).resolve()  # tools run with cwd=log/
    block = layout_gen.block_of(ws, args.block)
    bench_tpl, bounds_path = find_bench(ws, block)
    bounds = checklib.load_json(bounds_path, "pex_sim bounds")
    measures = bounds.get("measures")
    if not isinstance(measures, dict) or not measures:
        raise CheckError(f"{bounds_path} has no non-empty 'measures' map")

    import check_analog_lvs
    ref_path = check_analog_lvs.find_reference_netlist(ws, block)
    ref_text = ref_path.read_text(encoding="utf-8", errors="replace")
    ref_cell = check_analog_lvs.reference_cell(ws, block, ref_text)

    gds_path, topcell, _abs_path, _abstract = layout_gen.build(ws, block)

    work_dir = ws / "log" / "pex_sim"
    work_dir.mkdir(parents=True, exist_ok=True)
    raw, _log = layoutlib.run_magic_extract(
        work_dir, gds_path.resolve(), topcell, parasitics=True)
    raw_text = raw.read_text(encoding="utf-8", errors="replace")
    parasitics = layoutlib.count_parasitics(raw_text)
    if not parasitics["r"] and not parasitics["c"]:
        raise CheckError(
            f"{raw.name} carries no R and no C line - magic extracted no "
            "parasitics, so a bench on it is the schematic again")
    extracted = work_dir / f"{topcell}.pex.ordered.spice"
    layoutlib.fresh(extracted)
    extracted.write_text(reorder_pins(
        raw_text, topcell, pins_of(ref_text, ref_cell)), encoding="utf-8")

    pdk = layoutlib.pdk_root()
    bench_text = bench_tpl.read_text(encoding="utf-8").format(
        pdk=pdk, extracted=extracted.resolve())
    bench_path = work_dir / f"{block}_pex_tb.cir"
    layoutlib.fresh(bench_path)
    bench_path.write_text(bench_text, encoding="utf-8")

    sim_out = layoutlib.run_ngspice(bench_path, cwd=work_dir, timeout=120.0)
    values = layoutlib.parse_ngspice_prints(sim_out)
    if not values:
        raise CheckError(
            "ngspice produced no parseable 'name = value' print lines - "
            f"the bench did not measure anything: {sim_out[-2000:]}")

    violations = []
    measured = {}
    rel_bounds = str(bounds_path.relative_to(ws))
    for name, bound in measures.items():
        key = name.lower()
        if key not in values:
            violations.append(checklib.violation(
                "pex_sim", "error", rel_bounds, topcell, "measure_missing",
                [name], f"bench never printed a value for {name!r} "
                f"(printed: {sorted(values)})", "ngspice"))
            continue
        v = values[key]
        measured[name] = v
        lo, hi = bound.get("min"), bound.get("max")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            violations.append(checklib.violation(
                "pex_sim", "error", rel_bounds, topcell,
                "measure_out_of_bounds", [name],
                f"{name} = {v:.6g}, outside bound [{lo}, {hi}]", "ngspice"))

    payload = checklib.report(
        SCRIPT, ws / "layout", violations, topcell=topcell,
        gds=str(gds_path.relative_to(ws)),
        extracted=str(extracted.relative_to(ws)),
        parasitics=parasitics, measured=measured)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
