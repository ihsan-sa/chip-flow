"""lvs_top.py - section c of the macro_harden spike: netgen LVS of the whole
hardened tile, the analog macro included at transistor level.

    eda python lvs_top.py FINAL_GDS PNL_V MACRO_SPICE TOPCELL WORK_DIR \
        [--break-pin INSTANCE.PIN]

layout side     a fresh magic extraction of FINAL_GDS (not LibreLane's own
                final/spice), by LibreLane's own recipe (extract_spice.tcl):
                readspice the standard-cell spice first so magic numbers each
                cell's ports the way the spice library does, `load -dereference`,
                then plain extract + `ext2spice lvs`. The macro is just
                another subcell in the GDS, so it comes out as its two
                transistors.
schematic side  the standard-cell spice + MACRO_SPICE (the macro as a
                .subckt) + PNL_V, LibreLane's powered gate-level netlist -
                check_lvs.py's three-circuit recipe with one file added.
compare         netgen `lvs` with LibreLane's netgen setup.tcl, as
                check_lvs.py does.

--break-pin u_inv.in rewrites the copy of PNL_V this script reads so that
pin is left open (`.in()`), the netlist of a top that forgot to wire it,
and the result is expected NOT to match.

Prints one JSON line {"matched": bool, "final": "<netgen's last Final
result line>", ...}; exit 0 match, 1 no match, 2 the run did not finish.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "engine" / "scripts"))
sys.path.insert(0, str(REPO / "engine" / "lib"))
import check_lvs  # noqa: E402
import layoutlib  # noqa: E402

FINAL_RE = re.compile(r"^Final result:\s*(.*)$", re.MULTILINE)


def extract(gds: Path, top: str, stdcell_spice: Path, work: Path) -> Path:
    out = work / f"{top}.extracted.spice"
    layoutlib.fresh(out)
    script = "\n".join([
        "drc off", "crashbackups disable", "locking disable",
        f"gds read {gds}",
        f"readspice {stdcell_spice}",
        f"load {top} -dereference",
        "extract do local", "extract no capacitance", "extract no coupling",
        "extract no resistance", "extract no adjust",
        "extract", "ext2spice lvs", f"ext2spice -o {out.name} {top}.ext",
        "quit -noprompt", ""])
    proc = layoutlib.run_eda(["magic", "-noconsole", "-dnull"], cwd=work,
                             timeout=600, stdin_text=script)
    layoutlib.require_ok(proc, "magic extraction")
    if not out.is_file():
        raise layoutlib.LayoutError(f"magic wrote no {out.name}: "
                                    f"{(proc.stdout or '')[-1500:]}")
    return out


def break_pin(pnl_text: str, inst: str, pin: str) -> str:
    """Leave `.pin(...)` of instance `inst` open. Refuses when the
    instance or pin is not there, so a no-op edit can never pass as the
    negative case."""
    m = re.search(rf"\b{re.escape(inst)}\s*\((.*?)\);", pnl_text, re.S)
    if not m:
        raise SystemExit(f"no instance {inst} in the netlist")
    body = m.group(1)
    new_body, n = re.subn(rf"\.{re.escape(pin)}\s*\([^)]*\)", f".{pin}()", body)
    if n != 1:
        raise SystemExit(f"instance {inst} has no connected .{pin}(...)")
    return pnl_text[:m.start(1)] + new_body + pnl_text[m.end(1):]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("gds")
    ap.add_argument("pnl")
    ap.add_argument("macro_spice")
    ap.add_argument("top")
    ap.add_argument("work")
    ap.add_argument("--break-pin")
    args = ap.parse_args()
    gds, pnl = Path(args.gds).resolve(), Path(args.pnl).resolve()
    macro_spice = Path(args.macro_spice).resolve()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)

    pdk_root = check_lvs._pdk_root()
    pdk_dir = pdk_root / "gf180mcuD"
    models = [pdk_dir / rel for rel in check_lvs.PDK_SPICE_MODELS]
    setup_tcl = check_lvs._netgen_setup_tcl(pdk_root)

    try:
        spice = extract(gds, args.top, models[0], work)
    except layoutlib.LayoutError as exc:
        print(json.dumps({"error": str(exc)[-1500:]}))
        return 2

    pnl_used = pnl
    if args.break_pin:
        inst, pin = args.break_pin.split(".")
        pnl_used = work / f"{args.top}.broken.pnl.v"
        pnl_used.write_text(break_pin(pnl.read_text(), inst, pin))

    tag = "broken" if args.break_pin else "good"
    report = work / f"lvs_{tag}.rpt"
    script = work / f"lvs_{tag}.tcl"
    layoutlib.fresh(report)
    script.write_text("\n".join([
        f"set circuit1 [readnet spice {spice}]",
        "set circuit2 [readnet verilog /dev/null]",
        *[f"readnet spice {m} $circuit2" for m in models],
        f"readnet spice {macro_spice} $circuit2",
        f"readnet verilog {pnl_used} $circuit2",
        f'lvs "$circuit1 {args.top}" "$circuit2 {args.top}" {setup_tcl} '
        f"{report} -json",
        ""]))
    proc = layoutlib.run_eda(["netgen", "-batch", f"source {script}"],
                             cwd=work, timeout=600)
    text = report.read_text(errors="replace") if report.is_file() else ""
    finals = FINAL_RE.findall(text)
    if not finals:
        print(json.dumps({"error": "netgen wrote no Final result line",
                          "exit": proc.returncode,
                          "tail": ((proc.stdout or "") + (proc.stderr or ""))[-1500:]}))
        return 2
    matched = (finals[-1].strip().startswith("Circuits match uniquely")
               and "do not match" not in text
               and "property error" not in text.lower())
    macro_devices = sum(1 for ln in spice.read_text().splitlines()
                        if re.match(r"^X\S*\s.*\b[np]fet_03v3\b", ln))
    print(json.dumps({"matched": matched, "final": finals[-1].strip(),
                      "extracted": str(spice), "report": str(report),
                      "macro_fets_in_extraction": macro_devices}))
    return 0 if matched else 1


if __name__ == "__main__":
    raise SystemExit(main())
