#!/usr/bin/env python
"""check_top_lvs.py - msde's top_lvs gate (docs/design.md 1.5 msde table:
"netgen on the assembled GDS, the analog block as a subcircuit", match).

    check_top_lvs.py --workspace MSDE_WS [--out FILE]

layout side     a fresh magic extraction of the GDS top_harden left at
                top/harden/runs/run/final/gds, by LibreLane's own recipe
                (readspice the standard-cell spice first so each cell's ports
                come out numbered as the library has them, `load -dereference`,
                `ext2spice lvs`). The analog macro is just another subcell of
                that GDS, so it comes out as its transistors.
schematic side  the standard-cell spice, the analog cell's sized .subckt
                (top/macros/<cell>.spice) and LibreLane's powered gate-level
                netlist - M4's lvs recipe (check_lvs.py) with the macro added
                and without `-blackbox`, so the macro is compared device by
                device, never matched as an empty box.
setup           the PDK's own netgen setup (libs.tech/netgen/<pdk>_setup.tcl),
                the file LibreLane's Netgen.LVS step reaches through its
                wrapper setup.tcl. The wrapper itself is not used: it sources
                two variables only a LibreLane step sets, and outside one
                netgen skips it with a warning and compares with no device
                rules at all (source/drain not permutable, ad/as/pd/ps held
                as properties), which fails a clean design.

Pass is "Circuits match uniquely" with no subcell that failed to match and
no property error anywhere: the PDK setup drops the extraction-only
properties, so a property error left is a real W/L/nf difference.
Anything else is a `netlist_mismatch` finding. The run refuses (exit 2)
when netgen gives no verdict, when it reports errors reading the setup
file, and when the macro's cell in the extraction holds fewer
transistors (counted down its own hierarchy, never the standard cells')
than its .subckt has - the macro went in as a blackbox, and a match on
that would be hollow. The recipe is docs/spikes/macro_harden.md's.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "lib"))
import checklib  # noqa: E402
import check_lvs  # noqa: E402
import check_top_harden  # noqa: E402
import layoutlib  # noqa: E402
import speclib  # noqa: E402
import ttlib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_top_lvs"
FET_RE = re.compile(r"^[Xx]\S*\s.*\b[np]fet_\w+", re.MULTILINE)
FINAL_RE = re.compile(r"^Final result:\s*(.*)$", re.MULTILINE)
SETUP_ERR = "errors reading the setup file"
SUBCKT_RE = re.compile(r"^\.subckt\s+(\S+)(.*?)^\.ends\b", re.I | re.M | re.S)


def macro_fets(text: str, name: str) -> int:
    """Transistors under `.subckt name` in a spice text, counted down
    through any subcells it instantiates - 0 when it holds none or is not
    defined, which is what a blackboxed macro looks like."""
    bodies = {m.group(1): m.group(2) for m in SUBCKT_RE.finditer(text)}

    def count(cell: str, seen: frozenset) -> int:
        body = bodies.get(cell)
        if body is None or cell in seen:
            return 0
        n = len(FET_RE.findall(body))
        for line in body.splitlines():
            if line[:1] in "Xx" and not FET_RE.match(line):
                words = [w for w in line.split()[1:] if "=" not in w]
                n += count(words[-1], seen | {cell}) if words else 0
        return n
    return count(name, frozenset())


def netgen_setup(pdk_root: Path) -> Path:
    """The PDK's own netgen setup - see the module docstring for why not
    LibreLane's wrapper around it."""
    setup = (pdk_root / ttlib.PDK_NAME / "libs.tech" / "netgen"
             / f"{ttlib.PDK_NAME}_setup.tcl")
    if not setup.is_file():
        raise CheckError(f"no netgen setup for {ttlib.PDK_NAME} at {setup}")
    return setup


def judge(report: str, output: str) -> tuple[str, bool]:
    """(final verdict line, matched) from netgen's report and its console
    output. Refuses when netgen gave no verdict or ran without its setup."""
    finals = FINAL_RE.findall(report)
    if not finals:
        raise CheckError(f"netgen gave no LVS verdict: {output[-2000:]}")
    if SETUP_ERR in output or SETUP_ERR in report:
        raise CheckError("netgen reported errors reading its setup file, so "
                         "it compared with no device rules - no verdict: "
                         f"{output[-2000:]}")
    final = finals[-1].strip()
    matched = (final.startswith("Circuits match uniquely")
               and "do not match" not in report
               and "property error" not in report.lower())
    return final, matched


def extract(gds: Path, top: str, stdcell_spice: Path, work: Path) -> Path:
    out = work / f"{top}.extracted.spice"
    layoutlib.fresh(out)
    script = "\n".join([
        "drc off", "crashbackups disable", "locking disable",
        f"gds read {gds}", f"readspice {stdcell_spice}",
        f"load {top} -dereference",
        "extract do local", "extract no capacitance", "extract no coupling",
        "extract no resistance", "extract no adjust",
        "extract", "ext2spice lvs", f"ext2spice -o {out.name} {top}.ext",
        "quit -noprompt", ""])
    proc = layoutlib.run_eda(["magic", "-noconsole", "-dnull"], cwd=work,
                             timeout=900, stdin_text=script)
    layoutlib.require_ok(proc, "magic extraction")
    if not out.is_file():
        raise CheckError(f"magic wrote no {out.name}: "
                         f"{(proc.stdout or '')[-1500:]}")
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="msde block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    topdir = ws / check_top_harden.TOP_DIR
    spec_path = topdir / "spec" / "spec.yaml"
    if not spec_path.is_file():
        raise CheckError(f"no assembled top at {topdir} - has the top_harden "
                         "gate run?")
    spec = speclib.load_spec(spec_path)
    top = ttlib.wrapper_name(spec)
    final_dir = topdir / "harden" / "runs" / "run" / "final"
    gds = final_dir / "gds" / f"{top}.gds"
    pnl = final_dir / "pnl" / f"{top}.pnl.v"
    macro_spice = [Path(m["files"]["spice"]) for m in spec.get("macros") or []]
    for label, p in (("hardened GDS", gds), ("powered netlist", pnl),
                     *(("macro .subckt", s) for s in macro_spice)):
        if not p.is_file():
            raise CheckError(f"no {label} at {p} - has top_harden run?")

    pdk_root = check_lvs._pdk_root()
    setup_tcl = netgen_setup(pdk_root)
    models = [pdk_root / ttlib.PDK_NAME / rel
              for rel in check_lvs.PDK_SPICE_MODELS]
    # outside harden/ for the reason check_lvs.py gives: harden/ is hashed
    work = ws / "log" / "top_lvs"
    work.mkdir(parents=True, exist_ok=True)
    try:
        extracted = extract(gds, top, models[0], work)
    except layoutlib.LayoutError as exc:
        raise CheckError(str(exc)) from exc

    layout = extracted.read_text(errors="replace")
    want = got = 0
    for sp in macro_spice:
        src = sp.read_text(errors="replace")
        for name in {m.group(1) for m in SUBCKT_RE.finditer(src)}:
            want += macro_fets(src, name)
            got += macro_fets(layout, name)
    if want == 0 or got < want:
        raise CheckError(f"the extracted macro cell holds {got} transistor(s) "
                         f"but its .subckt has {want} - the macro went in as "
                         "a blackbox (or its .subckt is empty), so no LVS "
                         "verdict on it is possible")

    report = work / "top_lvs.rpt"
    script = work / "top_lvs.tcl"
    layoutlib.fresh(report)
    script.write_text("\n".join([
        f"set circuit1 [readnet spice {extracted}]",
        "set circuit2 [readnet verilog /dev/null]",
        *[f"readnet spice {m} $circuit2" for m in models],
        *[f"readnet spice {s} $circuit2" for s in macro_spice],
        f"readnet verilog {pnl} $circuit2",
        f'lvs "$circuit1 {top}" "$circuit2 {top}" {setup_tcl} {report} -json',
        ""]), encoding="utf-8")
    proc = layoutlib.run_eda(["netgen", "-batch", f"source {script}"],
                             cwd=work, timeout=900)
    text = report.read_text(errors="replace") if report.is_file() else ""
    output = (proc.stdout or "") + (proc.stderr or "")
    final, matched = judge(text, f"(exit {proc.returncode}) {output}")

    violations = []
    if not matched:
        excerpt = "\n".join(text.strip().splitlines()[-40:])
        violations.append(checklib.violation(
            "top_lvs", "error", None, top, "netlist_mismatch", [],
            f"netgen: the assembled layout does not match the powered "
            f"netlist with the analog macro: {final}\n{excerpt}", "netgen"))
    payload = checklib.report(SCRIPT, final_dir / "gds", violations, top=top,
                              matched=matched, final=final,
                              macro_fets_extracted=got, macro_fets_wanted=want,
                              report=str(report.relative_to(ws)))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
