#!/usr/bin/env python
"""check_analog_lvs.py - the ade `lvs` gate (docs/design.md 1.5, 5,
"### M9."; docs/spikes/glayout.md).

    check_analog_lvs.py --workspace DIR [--block NAME] [--out FILE]

gates.yaml's ade.lvs row names this tool `analog_lvs`, distinct from vde's
own `lvs` (netgen against a HARDENED digital netlist, M4). Regenerates the
GDS fresh (layout_gen.build(), same as check_analog_drc.py - never a stale
GDS), extracts it with magic (docs/spikes/glayout.md's own proven recipe:
"extract all; ext2spice"), then runs netgen against a REAL schematic
netlist - the spike's own LVS only compared gLayout's output with itself,
which "is not a real schematic-vs-layout LVS"; this one is.

Reference netlist lookup, in order:
  1. `netlist/<block>.spice` - a sized netlist whose device matches this
     rung's own layout generator exactly (same ratio, same pins).
  2. `layout_ref/<block>*.spice` - this M9 sitting's own minimal stand-in.
A workspace with neither is a refusal (CheckError), not an empty pass.

M8 (docs/design.md "### M8.") merged its own netlist/mirror.cir and
netlist/r2r_dac.cir after this gate was written, but neither is what (1)
above means: M8's mirror is a 2:1, 4u/8u device and M8's r2r_dac is a
2-bit ladder, while layout/gen_mirror.py and layout/gen_r2r_dac.py draw a
fixed 1:1, 0.22u/0.28u device and a fixed N=1 stage respectively (both
generators' own docstrings say why their geometry can't move). LVS against
M8's real netlist would fail on that genuine mismatch, not a planted
fault, so rung 2's stand-in stays the reference until a generator is built
to M8's own sizing.

A netgen result counts as PASS only on "Circuits match uniquely" with NO
"Property errors were found" (matching tests/check.sh's own
netgen-lvs-match/mismatch precedent, M0) - a topology match that still
disagrees on device sizing (docs/design.md 1.5's own planted ade.lvs fault:
"a device sized differently in the generator than in the netlist") must
still fail here.
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

SCRIPT = "check_analog_lvs"


def find_reference_netlist(ws: Path, block: str) -> Path:
    canonical = ws / "netlist" / f"{block}.spice"
    if canonical.is_file():
        return canonical
    ref_dir = ws / "layout_ref"
    if ref_dir.is_dir():
        candidates = sorted(ref_dir.glob(f"{block}*.spice"))
        if candidates:
            return candidates[0]
    raise CheckError(
        f"no reference schematic for LVS: looked for {canonical} (M8's "
        f"sized netlist) and {ref_dir}/{block}*.spice (M9's own stand-in) "
        "- a gate with nothing to compare against is a refusal, never a "
        "pass")


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--block", help="override state.json's 'block'")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    gds_path, topcell, _abs_path, _abstract = layout_gen.build(ws, args.block)
    block = args.block or topcell

    ref_spice = find_reference_netlist(ws, block)

    work_dir = ws / "log" / "lvs"
    work_dir.mkdir(parents=True, exist_ok=True)
    extracted_spice, extract_log = layoutlib.run_magic_extract(
        work_dir, gds_path.resolve(), topcell, parasitics=False)

    out_log = work_dir / "netgen_lvs.log"
    matched, lvs_text = layoutlib.run_netgen_lvs(
        work_dir, extracted_spice.name, topcell,
        ref_spice.resolve(), topcell, out_log.name)

    violations = []
    if not matched:
        excerpt = "\n".join(lvs_text.strip().splitlines()[-40:])
        violations.append(checklib.violation(
            "analog_lvs", "error", str(ref_spice.relative_to(ws))
            if ref_spice.is_relative_to(ws) else str(ref_spice),
            topcell, "lvs_mismatch", [],
            f"netgen LVS did not report a clean unique match: {excerpt}",
            "netgen"))

    payload = checklib.report(
        SCRIPT, ws / "layout", violations, topcell=topcell,
        gds=str(gds_path.relative_to(ws)), reference=str(ref_spice),
        log=str(out_log.relative_to(ws)))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
