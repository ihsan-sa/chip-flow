#!/usr/bin/env python
"""check_top_harden.py - msde's top_harden gate (docs/design.md "### M10.":
"the digital side hardens with the analog GDS as a LibreLane macro").

    check_top_harden.py --workspace MSDE_WS [--out FILE]

An msde block holds its two sides as nested workspaces, `digital/` (vde) and
`analog/` (ade). This gate assembles the chip top at `top/` and hardens it
with M4's own harden (check_harden.run, the same LibreLane job):

  top/spec/spec.yaml  the digital spec, minus tt_pins for every signal
                      interface.yaml names, plus a `macros:` entry binding
                      each of those signals to the analog cell's pin of the
                      same name (engine/lib/ttlib.py's macros schema)
  top/rtl/            a copy of digital/rtl
  top/macros/         the analog cell as LibreLane takes a macro: its GDS
                      (layout_gen, rebuilt from analog/layout/gen_<block>.py
                      like every ade layout gate does), a LEF abstract magic
                      writes from that GDS, a blackbox Verilog stub, and its
                      sized .subckt for LVS

Analog pins that interface.yaml's `ua_pins: {<pin>: <k>}` names leave the
chip on the Tiny Tapeout analog pad ua[k]: the top becomes an analog tile
(`tiles`, from interface.yaml or the analog template's default), hardened
on the vendored analog pin template, and LibreLane routes each such pin
to its pad (engine/lib/ttlib.py, "Analog tile").
Analog pins that interface.yaml names nowhere are its supplies; the one
named like vdd goes to VPWR and the one like vss to VGND (PDN macro hookup).
The macro sits in the middle of the tile. The recipe - LEF by plain `lef
write`, the PDN's Metal3-Metal4 connect, extraction from GDS - is
docs/spikes/macro_harden.md's.

Refuses (exit 2) when either nested workspace or a piece the assembly needs
is missing. An analog macro larger than the tile - by its LEF SIZE or by
what its GDS draws, whichever is larger - is a `macro_too_large` finding
(no harden runs); LibreLane failures are check_harden's findings,
passed on.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import layoutlib  # noqa: E402
import netlistlib  # noqa: E402
import ttlib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_top_harden"
TOP_DIR = "top"
INSTANCE = "u_analog"
VDD_RE = re.compile(r"^(a?vdd|vpwr|vcc)", re.IGNORECASE)
VSS_RE = re.compile(r"^(a?vss|gnd|vgnd)", re.IGNORECASE)


def _yaml(path: Path, what: str) -> dict:
    import yaml
    if not path.is_file():
        raise CheckError(f"no {what} at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CheckError(f"{path}: not a YAML mapping")
    return data


def nested(ws: Path, side: str, skill: str) -> Path:
    sub = ws / side
    data = checklib.load_json(sub / "state.json", f"{side}/state.json")
    if data.get("skill") != skill:
        raise CheckError(f"{side}/ is skill {data.get('skill')!r}, "
                         f"expected {skill!r}")
    return sub


def clean_gds(src: Path, dst: Path) -> None:
    """Re-write src without klayout's context-info cell. klayout hides that
    cell, but magic reads it as a real one, so the hardened GDS comes out
    with two top cells and LibreLane's KLayout.Render step fails
    (docs/spikes/macro_harden.md)."""
    import klayout.db as db
    layout = db.Layout()
    layout.read(str(src))
    opts = db.SaveLayoutOptions()
    opts.write_context_info = False
    layout.write(str(dst), opts)


def analog_macro(analog: Path, signals: dict[str, str], out_dir: Path,
                 ua: dict[str, int] | None = None) -> dict:
    """Write the analog cell's GDS, LEF, stub and .subckt under out_dir;
    return its ttlib `macros` entry (without location). `ua` maps analog
    pins to the ua pads they go out on."""
    ua = ua or {}
    import check_analog_lvs
    import layout_gen
    import sim_run

    block = layout_gen.block_of(analog, None)
    gds, cell, _abs_path, _abstract = layout_gen.build(analog, block)
    ref_path = check_analog_lvs.find_reference_netlist(analog, block)
    ref_text = ref_path.read_text(encoding="utf-8", errors="replace")
    ref_cell = check_analog_lvs.reference_cell(analog, block, ref_text)
    if ref_cell.lower() != cell.lower():
        raise CheckError(f"the analog layout's top cell {cell!r} is not the "
                         f"schematic's .subckt {ref_cell!r}")
    sizing = sim_run.load_sizing(analog)
    if sizing:
        ref_text, _applied = check_analog_lvs.sized_reference(
            ref_text, ref_cell, sizing)
    pins = [p for p in netlistlib.subckt_pins(ref_text)[ref_cell.lower()]
            if "=" not in p]  # drop .subckt parameter defaults

    missing = sorted((set(signals) | set(ua)) - {p.lower() for p in pins})
    if missing:
        raise CheckError(f"interface.yaml names {missing}, which the analog "
                         f".subckt {ref_cell} ({pins}) does not have")
    both = sorted(set(signals) & set(ua))
    if both:
        raise CheckError(f"interface.yaml names {both} both as a signal and "
                         "in ua_pins; a pin crosses to the digital side or "
                         "goes out on a pad, not both")
    supplies = [p for p in pins
                if p.lower() not in signals and p.lower() not in ua]
    vdd = [p for p in supplies if VDD_RE.match(p)]
    vss = [p for p in supplies if VSS_RE.match(p)]
    if len(vdd) != 1 or len(vss) != 1 or len(supplies) != 2:
        raise CheckError(f"the analog cell's non-interface pins {supplies} "
                         "must be exactly one supply (vdd...) and one ground "
                         "(vss/gnd...)")
    vdd, vss = vdd[0], vss[0]

    out_dir.mkdir(parents=True, exist_ok=True)
    gds_out = out_dir / f"{cell}.gds"
    clean_gds(gds, gds_out)
    spice_out = out_dir / f"{cell}.spice"
    spice_out.write_text(ref_text, encoding="utf-8")

    # a2d: the analog side drives it, so it is the macro's output.
    port_lines = []
    for sig, direction in sorted(signals.items()):
        cls = "output" if direction == "a2d" else "input"
        port_lines += [f"port {sig} use signal", f"port {sig} class {cls}"]
    for pin in sorted(ua):
        port_lines += [f"port {pin} use signal", f"port {pin} class inout"]
    lef_out = out_dir / f"{cell}.lef"
    layoutlib.fresh(lef_out)
    script = "\n".join([
        "drc off", "crashbackups disable", "locking disable",
        f"gds read {gds_out.name}", f"load {cell}", "select top cell",
        "port makeall", *port_lines,
        f"port {vdd} use power", f"port {vdd} class inout",
        f"port {vss} use ground", f"port {vss} class inout",
        "property LEFclass BLOCK", f"lef write {lef_out.name}",
        "quit -noprompt", ""])
    proc = layoutlib.run_eda(["magic", "-noconsole", "-dnull"], cwd=out_dir,
                             timeout=300, stdin_text=script)
    layoutlib.require_ok(proc, "magic lef write")
    if not lef_out.is_file():
        raise CheckError(f"magic wrote no {lef_out.name}: "
                         f"{(proc.stdout or '')[-1500:]}")

    sig_decls = [f"    {'output' if d == 'a2d' else 'input '} wire {s}"
                 for s, d in sorted(signals.items())]
    sig_decls += [f"    inout  wire {pin}" for pin in sorted(ua)]
    stub = out_dir / f"{cell}.v"
    stub.write_text("\n".join([
        f"// {cell}.v - generated by check_top_harden.py: the blackbox the",
        "// synthesiser sees for the analog macro. Power pins exist only",
        "// under USE_POWER_PINS (PDN_MACRO_CONNECTIONS ties them).",
        "(* blackbox *)",
        f"module {cell} (",
        "`ifdef USE_POWER_PINS",
        f"    inout  wire {vdd},", f"    inout  wire {vss},",
        "`endif",
        ",\n".join(sig_decls),
        ");", "endmodule", ""]), encoding="utf-8")

    return {"cell": cell, "instance": INSTANCE, "orientation": "N",
            "power": {"vdd": vdd, "vss": vss},
            "pins": {s: s for s in sorted(signals)},
            **({"ua": dict(sorted(ua.items()))} if ua else {}),
            "files": {"gds": str(gds_out.resolve()),
                      "lef": str(lef_out.resolve()),
                      "vh": str(stub.resolve()),
                      "spice": str(spice_out.resolve())}}


def lef_size(lef: Path) -> tuple[float, float]:
    m = re.search(r"^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", lef.read_text(),
                  re.MULTILINE)
    if not m:
        raise CheckError(f"{lef} carries no SIZE")
    return float(m.group(1)), float(m.group(2))


def gds_extent(gds: Path, cell: str) -> tuple[float, float]:
    """Width and height of everything drawn in the cell. magic's LEF SIZE
    follows the cell's declared boundary, so metal drawn past it would not
    show in lef_size; the tile has to hold the drawn extent."""
    import klayout.db as db
    layout = db.Layout()
    layout.read(str(gds))
    box = layout.cell(cell).dbbox()
    return round(box.width(), 3), round(box.height(), 3)


def macro_size(lef: Path, gds: Path, cell: str) -> tuple[float, float]:
    """The larger of the LEF SIZE and the drawn extent, per axis."""
    return tuple(max(a, b) for a, b in zip(lef_size(lef),
                                           gds_extent(gds, cell)))


def centre(die_area: str, w: float, h: float) -> list[float] | None:
    """Lower-left that centres a w x h macro in the die, on the gf180
    7-track site grid (0.56 um) and row pitch (3.92 um); None when the
    macro is larger than the die."""
    _x0, _y0, dx, dy = (float(v) for v in die_area.split())
    if w > dx or h > dy:
        return None
    return [round(round((dx - w) / 2 / 0.56) * 0.56, 2),
            round(round((dy - h) / 2 / 3.92) * 3.92, 2)]


def assemble(ws: Path) -> tuple[Path, dict]:
    """Build top/ from the two nested workspaces; return its path and spec.
    A macro larger than the tile comes back with location None and no
    spec.yaml written - a design finding, not a refusal."""
    digital = nested(ws, "digital", "vde")
    analog = nested(ws, "analog", "ade")
    iface = _yaml(ws / "interface.yaml", "interface.yaml")
    signals = {s["name"].lower(): s["direction"]
               for s in iface.get("signals") or []}
    if not signals:
        raise CheckError("interface.yaml names no signals")
    ua = {str(p).lower(): k for p, k in (iface.get("ua_pins") or {}).items()}

    dspec = _yaml(digital / "spec" / "spec.yaml", "digital spec.yaml")
    ports = {p.lower(): p for p in dspec.get("ports") or {}}
    missing = sorted(set(signals) - set(ports))
    if missing:
        raise CheckError(f"interface.yaml names {missing}, which the digital "
                         "spec.yaml's ports do not have")

    top = ws / TOP_DIR
    if top.exists():
        shutil.rmtree(top)
    (top / "spec").mkdir(parents=True)
    shutil.copytree(digital / "rtl", top / "rtl")

    macro = analog_macro(analog, signals, top / "macros", ua)
    macro["pins"] = {pin: ports[pin] for pin in macro["pins"]}
    size = macro_size(Path(macro["files"]["lef"]),
                      Path(macro["files"]["gds"]), macro["cell"])
    tspec = dict(dspec)
    if iface.get("tiles"):
        tspec["tiles"] = str(iface["tiles"])
    tspec["macros"] = [macro]
    tiles = ttlib.spec_tiles(tspec)
    macro["location"] = centre(ttlib.tile_die_area(tiles), *size)
    macro["size_um"] = list(size)
    tspec["tt_pins"] = {k: v for k, v in (dspec.get("tt_pins") or {}).items()
                        if k.lower() not in signals}
    if macro["location"] is None:
        return top, tspec
    problems = ttlib.validate_tt_pins(tspec)
    if problems:
        raise CheckError("the assembled top's pins do not fit: "
                         + "; ".join(problems))
    import yaml
    (top / "spec" / "spec.yaml").write_text(
        yaml.safe_dump(tspec, sort_keys=False), encoding="utf-8")
    return top, tspec


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="msde block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    try:
        top, spec = assemble(ws)
    except layoutlib.LayoutError as exc:
        raise CheckError(str(exc)) from exc
    macros = [{k: m[k] for k in ("cell", "instance", "location", "size_um",
                                 "pins")} for m in spec["macros"]]
    too_big = [m for m in macros if m["location"] is None]
    tiles = ttlib.spec_tiles(spec)
    if too_big:
        violations = [checklib.violation(
            "top_harden", "error", None, m["cell"], "macro_too_large", [],
            f"the analog macro {m['cell']} is {m['size_um'][0]} x "
            f"{m['size_um'][1]} um, larger than the tile "
            f"({tiles}: {ttlib.tile_die_area(tiles)})", SCRIPT)
            for m in too_big]
        payload = checklib.report(SCRIPT, ws / "digital" / "rtl", violations,
                                  macros=macros)
        return payload, args.out
    import check_harden
    inner, _out = check_harden.run(["--workspace", str(top)])
    facts = {k: inner[k] for k in ("top", "run_tag", "resumed", "wall_s",
                                   "run_dir", "metrics") if k in inner}
    payload = checklib.report(SCRIPT, ws / "digital" / "rtl",
                              inner.get("violations", []), macros=macros,
                              **facts)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
