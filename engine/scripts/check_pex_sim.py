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

Ground is checked before the bench runs. A reference subckt that reaches
ngspice's global ground (a node `0`, or its alias `gnd`) without a port for
it needs the extracted netlist to carry a node of that name too - magic
names a net `0` when the layout puts a non-pin text label "0" on it (a
drawing-layer label such as metal1 34/0; a pin-layer label would make it a port and change the pin set).
Without one, the layout's ground is a local net of magic's own naming and
the bench runs with it floating. The gate never guesses which extracted net
is ground - a substrate net and a ground wire both carry magic-made names,
and tying the wrong one would pass a layout that is wired wrong - so a
missing ground label is a `ground_unlabelled` finding for the layout
generator, and no bench is run on that netlist.

Probes are checked the same way, before the bench runs. A bench that reads
a net inside the extracted cell (`v(xdut.s1)` in its .control block, where
`xdut` instantiates the top cell) needs that net to keep its name through
extraction, and magic keeps a name only where the layout puts a text label
on the net. An unlabelled net comes back as `a_..#`, and ngspice then
either warns that the vector is not available or refuses the whole run
with an error that names no net. So a probed net that the reference
netlist has and the extracted netlist does not is a `probe_unlabelled`
finding for the layout generator, one per net, and no bench is run. A
probe of a net the reference does not have either is a bench bug, not a
layout one, and is left to ngspice to refuse.

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


# ngspice's global ground: node 0, and `gnd`, which it accepts as an alias
GROUND = {"0", "gnd"}

# node count per element letter; `x` is every token before the subckt name
NODE_COUNT = {"r": 2, "c": 2, "l": 2, "d": 2, "v": 2, "i": 2, "b": 2,
              "f": 2, "h": 2, "w": 2, "e": 4, "g": 4, "m": 4, "s": 4,
              "t": 4, "o": 4, "q": 3, "j": 3, "z": 3, "u": 3}


def element_nodes(netlist_text: str) -> set[str]:
    """Every node name (lowercased) an element line of netlist_text touches.
    Dot-cards and a .control block are not elements; a letter NODE_COUNT
    does not know contributes nothing rather than a guess."""
    nodes: set[str] = set()
    in_control = False
    for line in netlistlib.join_continuations(netlist_text):
        tokens = line.split()
        low = tokens[0].lower()
        if low.startswith(".control"):
            in_control = True
            continue
        if low.startswith(".endc"):
            in_control = False
            continue
        if in_control or low[0] in ".$;":
            continue
        rest = [t for t in tokens[1:] if "=" not in t]
        if low[0] == "x":
            nodes.update(t.lower() for t in rest[:-1])
        else:
            nodes.update(t.lower() for t in rest[:NODE_COUNT.get(low[0], 0)])
    return nodes


def ground_finding(ref_text: str, ref_pins: list[str], extracted_text: str,
                   topcell: str, rel_gds: str) -> dict | None:
    """A `ground_unlabelled` finding when the reference reaches global
    ground without a port for it and the extracted netlist has no node of
    that name; None when there is nothing to tie or the layout names it."""
    pins = {p.lower() for p in ref_pins}
    used = (element_nodes(ref_text) & GROUND) - pins
    if not used or element_nodes(extracted_text) & GROUND:
        return None
    return checklib.violation(
        "pex_sim", "error", rel_gds, topcell, "ground_unlabelled",
        sorted(used),
        f"the reference {topcell} ties devices to global ground "
        f"({', '.join(sorted(used))}) with no port for it, and the "
        "extracted netlist has no node 0 - the layout's ground is a local "
        "net of magic's own naming, so the bench would run with it "
        "floating. Fix in layout/gen_<block>.py (the layout-fixer): put a "
        "non-pin text label \"0\" on the ground net's drawing layer (e.g. "
        "metal1 34/0), "
        "not on a pin/label layer, which would add a port and change the "
        "pin set", "magic")


# a voltage probe in a .control block: v(), vdb(), vm(), vp(), vr(), vi()
PROBE_RE = re.compile(r"\bv(?:db|m|p|r|i)?\(([^()]*)\)", re.IGNORECASE)


def probed_nets(bench_text: str, topcell: str) -> set[str]:
    """Every net (lowercased) the bench's .control block probes one level
    inside an instance of `topcell` - `net` for `v(xdut.net)`. A top-level
    node or a deeper path is not the extracted cell's own net name."""
    lines = netlistlib.join_continuations(bench_text)
    insts = set()
    for line in lines:
        tokens = [t for t in line.split() if "=" not in t]
        if (len(tokens) > 1 and tokens[0][0] in "xX"
                and tokens[-1].lower() == topcell.lower()):
            insts.add(tokens[0].lower())
    nets: set[str] = set()
    in_control = False
    for line in lines:
        low = line.split()[0].lower()
        if low.startswith(".control"):
            in_control = True
        elif low.startswith(".endc"):
            in_control = False
        elif in_control:
            for m in PROBE_RE.finditer(line):
                for arg in m.group(1).split(","):
                    parts = arg.strip().lower().split(".")
                    if len(parts) == 2 and parts[0] in insts and parts[1]:
                        nets.add(parts[1])
    return nets


def probe_findings(bench_text: str, ref_text: str, extracted_text: str,
                   topcell: str, rel_gds: str) -> list[dict]:
    """A `probe_unlabelled` finding for each net the bench probes inside
    the top cell that the reference has and the extraction does not."""
    missing = ((probed_nets(bench_text, topcell) & element_nodes(ref_text))
               - element_nodes(extracted_text))
    return [checklib.violation(
        "pex_sim", "error", rel_gds, topcell, "probe_unlabelled", [net],
        f"the pex bench probes {topcell}'s net {net!r}, which the reference "
        f"netlist has, but the extracted netlist has no node {net!r} - magic "
        "keeps a net's name only where the layout labels it, so the bench "
        "would read a vector that does not exist. Fix in "
        "layout/gen_<block>.py (the layout-fixer): put a non-pin text label "
        f"\"{net}\" on that net's drawing layer (e.g. metal1 34/0), not on "
        "a pin/label layer, which would add a port and change the pin set",
        "magic") for net in sorted(missing)]


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
    ref_pins = pins_of(ref_text, ref_cell)
    extracted.write_text(reorder_pins(raw_text, topcell, ref_pins),
                         encoding="utf-8")

    pdk = layoutlib.pdk_root()
    bench_text = bench_tpl.read_text(encoding="utf-8").format(
        pdk=pdk, extracted=extracted.resolve())

    # a floating ground or an unlabelled probe is not a bench worth running:
    # the findings alone
    rel_gds = str(gds_path.relative_to(ws))
    ground = ground_finding(ref_text, ref_pins, raw_text, topcell, rel_gds)
    unrun = ([ground] if ground is not None else []) + probe_findings(
        bench_text, ref_text, raw_text, topcell, rel_gds)
    if unrun:
        payload = checklib.report(
            SCRIPT, ws / "layout", unrun, topcell=topcell, gds=rel_gds,
            extracted=str(extracted.relative_to(ws)),
            parasitics=parasitics, measured={})
        return payload, args.out

    bench_path = work_dir / f"{block}_pex_tb.cir"
    layoutlib.fresh(bench_path)
    bench_path.write_text(bench_text, encoding="utf-8")

    # a meas whose trigger/target never happened (a latch that decided the
    # wrong way never lets outn fall) comes back by name and is a finding
    # below; any other ngspice error still refuses the gate
    never_met: set[str] = set()
    sim_out = layoutlib.run_ngspice(bench_path, cwd=work_dir, timeout=120.0,
                                    failed_measures=never_met)
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
        if key in never_met and key not in values:
            violations.append(checklib.violation(
                "pex_sim", "error", rel_bounds, topcell, "measure_missing",
                [name], f"{name}: the bench's trigger/target condition was "
                "never met (ngspice: 'failed!') - the circuit never made "
                "the transition this measure times", "ngspice"))
            continue
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
