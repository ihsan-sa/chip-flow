#!/usr/bin/env python
"""check_analog_lvs.py - the ade `lvs` gate (docs/design.md 1.5, 5,
"### M9."; docs/spikes/glayout.md).

    check_analog_lvs.py --workspace DIR [--block NAME] [--out FILE]

gates.yaml's ade.lvs row names this tool `analog_lvs`, distinct from vde's
own `lvs` (netgen against a hardened digital netlist, M4). Regenerates the
GDS fresh (layout_gen.build(), same as check_analog_drc.py - never a stale
GDS), extracts it with magic, then runs netgen under the PDK's own setup
(libs.tech/netgen/<pdk>_setup.tcl) against the block's real schematic.

The schematic is M8's own netlist: `netlist/<block>.cir`, or
`netlist/<block>.spice`. There is no fallback. A workspace without one is a
refusal, not an empty pass. The cell compared is spec/spec.yaml's `top`
(M8's subckt name, `current_mirror` for the mirror), or the block name
when the spec has none.

When the block has sizing/sizing.yaml, the subckt's own parameter defaults
are replaced by the sizing values in a copy under log/lvs/ before netgen
reads it - the same values `{{SIZING}}` hands the bench - so LVS checks the
layout against the sizing the design is simulated at, not against whatever
defaults the netlist happened to ship.

A netgen result counts as PASS only on a final "Circuits match uniquely."
with no property error anywhere in the log (layoutlib.run_netgen_lvs), so a
device sized differently in the generator than in the netlist (gates.yaml's
own planted ade.lvs fault) fails here even though the topology matches.
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

SCRIPT = "check_analog_lvs"


def find_reference_netlist(ws: Path, block: str) -> Path:
    for ext in (".cir", ".spice"):
        p = ws / "netlist" / f"{block}{ext}"
        if p.is_file():
            return p
    raise CheckError(
        f"no reference schematic for LVS: looked for netlist/{block}.cir "
        f"and netlist/{block}.spice (M8's netlist) - a gate with nothing to "
        "compare against is a refusal, never a pass")


def reference_cell(ws: Path, block: str, ref_text: str) -> str:
    cell = block
    spec = ws / "spec" / "spec.yaml"
    if spec.is_file():
        import yaml
        data = yaml.safe_load(spec.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("top"):
            cell = str(data["top"])
    if cell.lower() not in netlistlib.subckt_pins(ref_text):
        raise CheckError(
            f"the reference netlist declares no .subckt {cell} (spec.yaml's "
            "top, or the block name) - nothing to compare the layout with")
    return cell


def sized_reference(ref_text: str, cell: str, sizing: dict) -> tuple[str, dict]:
    """ref_text with `cell`'s own `.subckt` parameter defaults replaced by
    the sizing values that name them. Returns (text, {name: value applied}).
    A sizing name the subckt does not declare is left alone: it may be a
    bench-level .param."""
    applied: dict[str, float] = {}
    out = []
    head = re.compile(rf"^\s*\.subckt\s+{re.escape(cell)}\s", re.IGNORECASE)
    for line in ref_text.splitlines(keepends=True):
        if head.match(line):
            for name, spec in sizing.items():
                val = spec["value"] if isinstance(spec, dict) else spec
                pat = re.compile(rf"(\s{re.escape(name)}\s*=\s*)[^\s]+",
                                 re.IGNORECASE)
                if pat.search(line):
                    line = pat.sub(lambda m: f"{m.group(1)}{val:.6g}", line,
                                   count=1)
                    applied[name] = val
        out.append(line)
    return "".join(out), applied


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--block", help="override state.json's 'block'")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    block = layout_gen.block_of(ws, args.block)
    ref_path = find_reference_netlist(ws, block)
    ref_text = ref_path.read_text(encoding="utf-8", errors="replace")
    ref_cell = reference_cell(ws, block, ref_text)

    gds_path, topcell, _abs_path, _abstract = layout_gen.build(ws, block)

    work_dir = ws / "log" / "lvs"
    work_dir.mkdir(parents=True, exist_ok=True)

    import sim_run
    sizing = sim_run.load_sizing(ws)
    ref_for_lvs = ref_path.resolve()
    applied = {}
    if sizing:
        text, applied = sized_reference(ref_text, ref_cell, sizing)
        if applied:
            ref_for_lvs = work_dir / f"{block}.sized{ref_path.suffix}"
            ref_for_lvs.write_text(text, encoding="utf-8")

    extracted_spice, _extract_log = layoutlib.run_magic_extract(
        work_dir, gds_path.resolve(), topcell, parasitics=False)

    out_log = work_dir / "netgen_lvs.log"
    matched, lvs_text = layoutlib.run_netgen_lvs(
        work_dir, extracted_spice.name, topcell,
        ref_for_lvs, ref_cell, out_log.name)

    rel_ref = str(ref_path.relative_to(ws))
    violations = []
    if not matched:
        excerpt = "\n".join(lvs_text.strip().splitlines()[-40:])
        violations.append(checklib.violation(
            "analog_lvs", "error", rel_ref, ref_cell, "lvs_mismatch", [],
            f"netgen LVS did not report a clean unique match: {excerpt}",
            "netgen"))

    payload = checklib.report(
        SCRIPT, ws / "layout", violations, topcell=topcell,
        gds=str(gds_path.relative_to(ws)), reference=rel_ref,
        reference_cell=ref_cell, sizing_applied=applied,
        log=str(out_log.relative_to(ws)))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
