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
                      same name, or a width-N signal to its N pins
                      <name>[N-1]..<name>[0] (engine/lib/ttlib.py's macros
                      schema)
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
The macro sits in the middle of the tile. The recipe - LEF by magic's `lef
write`, the PDN's Metal3-Metal4 connect, extraction from GDS - is
docs/spikes/macro_harden.md's, except in two ways. The LEF abstract covers
everything the macro draws, never a PR boundary smaller than that (one a
flattened standard cell left behind); magic writing an abstract more than
1 um short of the drawn extent on either axis is a refusal. And the LEF is
written -pinonly: each port's pin is the rectangle of metal under its label
(a supply's adds its net's Metal3) and the rest of the macro is OBS, so the
router never lands on a port's internal wiring. A port label on no metal of
its own layer, or a port the LEF gives no pin 0.23 um a side, is a refusal;
a label that names no port is dropped, its metal OBS. The macro's GDS
is moved so what it draws starts at (0, 0), and a LEF ORIGIN other than
0 0 is a refusal: the final GDS puts the cell's origin where OpenROAD put
the abstract's lower-left, so the two must coincide. Before the LEF is
written the macro gets the PDK's dummy-COMP fill (fill_macro, the rules of
the PDK's fill_comp.rb), since the top's fill skips a macro and DCF.1a
judges the macro's inside at top_drc and the precheck. The tile's
Metal4 stripes join the macro on Metal3 only, so a supply pin with no
Metal3 shape is a `macro_power_unreachable` finding (no harden runs).

Refuses (exit 2) when either nested workspace or a piece the assembly needs
is missing. An analog macro larger than the tile - by its LEF SIZE or by
what its GDS draws, whichever is larger - is a `macro_too_large` finding
(no harden runs); so is a `ua_pins` map off the analog template - an
index used twice, a gap below the highest pad, more pads than the tile has
(`ua_pin_off_template`, no harden runs). LibreLane failures are
check_harden's findings, passed on.
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
# the layer engine/lib/macro_pdn.tcl joins to the tile's Metal4 stripes
MACRO_POWER_LAYER = "Metal3"


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


def clean_gds(src: Path, dst: Path, cell: str) -> None:
    """Re-write src without klayout's context-info cell, with `cell` moved
    so what it draws starts at (0, 0). klayout hides the context cell, but
    magic reads it as a real one, so the hardened GDS comes out with two
    top cells and LibreLane's KLayout.Render step fails
    (docs/spikes/macro_harden.md). A cell drawn below or left of its origin
    makes magic write a LEF ORIGIN: OpenROAD routes to the abstract's
    lower-left at the DEF point but the final GDS puts the cell's origin
    there, so every pin lands off its route (ece298a's R-2R DAC, a Metal3
    vss strap below y=0, 12 LVS errors)."""
    import klayout.db as db
    layout = db.Layout()
    layout.read(str(src))
    top = layout.cell(cell)
    if top is None:
        raise CheckError(f"{src.name} has no cell {cell!r}")
    box = top.bbox()
    if not box.empty() and (box.left or box.bottom):
        top.transform(db.Trans(-box.left, -box.bottom))
    opts = db.SaveLayoutOptions()
    opts.write_context_info = False
    layout.write(str(dst), opts)


# Dummy-COMP fill, as the PDK's own libs.tech/klayout/tech/scripts/
# fill_comp.rb draws it: 5 um squares on COMP_Dummy, 3 um apart (DCF.2a),
# each row stepped 1.6 um (DCF.3), kept off every layer that script keeps
# it off, by the same distance (um).
COMP_DUMMY = (22, 4)
FILL_SQUARE = 5.0
FILL_SPACE = 3.0
FILL_SKEW = 1.6
FILL_KEEPOUT = {          # (layer, datatype): space, the rule that sets it
    (22, 0): 3.5,         # COMP, DCF.4
    (22, 4): FILL_SPACE,  # COMP_Dummy already there, DCF.2a
    (30, 0): 1.5,         # Poly2, DCF.5
    (110, 5): 3.5,        # RES_MK, DCF.8a
    (37, 0): 7.0,         # Pad, DCF.9
    (111, 5): 3.5,        # NDMY, DCF.11a
    (151, 5): 3.0,        # IND_MK, DCF.12
}
FILL_RING = {             # well edges, filled neither side (DCF.6abcd)
    (21, 0): 1.3,         # Nwell
    (12, 0): 4.0,         # DNWELL
    (204, 0): 1.3,        # LVPWELL
    (55, 0): 1.3,         # Dualgate
}
# fill_comp.rb keeps 26 um off a die's edge for the scribe line (DCF.7a); a
# macro's edge is inside the die, so the fill runs right up to it. Nothing
# the top draws comes near: LibreLane keeps its 10 um macro halo free of
# standard cells, past DCF.4's 3.5 um and every well space above, and that
# halo is bare COMP, so a strip the fill left bare inside the macro's edge
# would add to it towards DCF.1a's 20 um.


def fill_macro(gds: Path, cell: str) -> int:
    """Draw dummy COMP into `cell` of gds, in place, and return how many
    squares went in. The top's fill treats the macro as a blockage, and the
    block's own DRC leaves the dummy decks off (layoutlib.DRC_SKIPPED), so
    without this nothing fills it and top_drc and the precheck each fail
    DCF.1a on any 20 um of the macro with no COMP (ece298a's R-2R DAC,
    breakage 37). COMP_Dummy is the only fill the decks demand - the dummy
    poly and metal decks only space fill that exists, and density is off.
    It lies under every metal, and magic reads it as filldiff, which makes
    no device and connects to nothing, so LVS and the LEF's pins and OBS
    are as they were. The squares go in as plain shapes, inside the
    drawn extent, so the extent the LEF and macro_size read is unchanged."""
    import klayout.db as db
    layout = db.Layout()
    layout.read(str(gds))
    top = layout.cell(cell)
    if top is None:
        raise CheckError(f"{gds.name} has no cell {cell!r}")
    if top.bbox().empty():
        return 0
    um = 1 / layout.dbu

    def shapes(spec) -> "db.Region":
        idx = layout.find_layer(*spec)
        if idx is None:
            return db.Region()
        return db.Region(top.begin_shapes_rec(idx)).merged()

    frame = db.Region(top.bbox())
    keep_out = db.Region()
    for spec, space in FILL_KEEPOUT.items():
        keep_out += shapes(spec).sized(round(space * um))
    for spec, space in FILL_RING.items():
        well = shapes(spec)
        keep_out += (well.sized(round(space * um))
                     - well.sized(round(-space * um)))
    region = frame - keep_out
    if region.is_empty():
        return 0

    scratch = layout.create_cell(f"{cell}__fill")
    square = layout.create_cell(f"{cell}__fill_square")
    box = db.Box(0, 0, round(FILL_SQUARE * um), round(FILL_SQUARE * um))
    dummy = layout.layer(*COMP_DUMMY)
    square.shapes(dummy).insert(box)
    pitch = round((FILL_SQUARE + FILL_SPACE) * um)
    skew = round(FILL_SKEW * um)
    scratch.fill_region(region, square.cell_index(), box,
                        db.Vector(pitch, skew), db.Vector(skew, pitch),
                        db.Point(), None, db.Vector(), None)
    scratch.flatten(True)
    # a skewed fill_region can place one square more than once; merged,
    # each is drawn once (none touch another: they are 3 um apart)
    fill = db.Region(scratch.shapes(dummy)).merged()
    top.shapes(dummy).insert(fill)
    for tmp in (scratch, layout.cell(f"{cell}__fill_square")):
        if tmp is not None:
            layout.delete_cell(tmp.cell_index())
    opts = db.SaveLayoutOptions()
    opts.write_context_info = False
    layout.write(str(gds), opts)
    return fill.count()


def signal_pins(signals: dict[str, str],
                widths: dict[str, int]) -> dict[str, str]:
    """{analog .subckt pin: signal} - a width-N interface signal is the N
    pins `<name>[N-1]`..`<name>[0]`, the names a bus bit takes in a SPICE
    netlist, a GDS label and the LEF magic writes; width 1 is the bare name."""
    out: dict[str, str] = {}
    for sig in signals:
        w = int(widths.get(sig, 1))
        for pin in ([f"{sig}[{i}]" for i in range(w)] if w > 1 else [sig]):
            out[pin] = sig
    return out


def tcl_tok(name: str) -> str:
    """Brace a magic Tcl token. Magic reads its script as Tcl, so an unbraced
    bus-bit pin name like dac_code[0] runs [0] as a nested command and the
    port command silently sees a bare, mangled name - braces make it a
    literal token instead (engine/scripts/check_drc.py:91 escapes brackets
    the same way, for the same reason)."""
    return "{" + name + "}"


def port_lines(sig_pins: dict[str, str], signals: dict[str, str],
              ua: dict[str, int]) -> list[str]:
    """The `port ... use ...` / `port ... class ...` magic Tcl lines for
    every interface signal pin (a2d pins are macro outputs, d2a are inputs)
    and every ua pad pin (inout), each pin name braced per tcl_tok."""
    lines = []
    for pin, sig in sorted(sig_pins.items()):
        cls = "output" if signals[sig] == "a2d" else "input"
        lines += [f"port {tcl_tok(pin)} use signal",
                 f"port {tcl_tok(pin)} class {cls}"]
    for pin in sorted(ua):
        lines += [f"port {tcl_tok(pin)} use signal",
                 f"port {tcl_tok(pin)} class inout"]
    return lines


def analog_macro(analog: Path, signals: dict[str, str], out_dir: Path,
                 ua: dict[str, int] | None = None,
                 widths: dict[str, int] | None = None) -> dict:
    """Write the analog cell's GDS, LEF, stub and .subckt under out_dir;
    return its ttlib `macros` entry (without location). `ua` maps analog
    pins to the ua pads they go out on."""
    ua = ua or {}
    widths = widths or {}
    sig_pins = signal_pins(signals, widths)
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

    missing = sorted((set(sig_pins) | set(ua)) - {p.lower() for p in pins})
    if missing:
        raise CheckError(f"interface.yaml names {missing}, which the analog "
                         f".subckt {ref_cell} ({pins}) does not have")
    both = sorted(set(signals) & set(ua))
    if both:
        raise CheckError(f"interface.yaml names {both} both as a signal and "
                         "in ua_pins; a pin crosses to the digital side or "
                         "goes out on a pad, not both")
    supplies = [p for p in pins
                if p.lower() not in sig_pins and p.lower() not in ua]
    vdd = [p for p in supplies if VDD_RE.match(p)]
    vss = [p for p in supplies if VSS_RE.match(p)]
    if len(vdd) != 1 or len(vss) != 1 or len(supplies) != 2:
        raise CheckError(f"the analog cell's non-interface pins {supplies} "
                         "must be exactly one supply (vdd...) and one ground "
                         "(vss/gnd...)")
    vdd, vss = vdd[0], vss[0]

    out_dir.mkdir(parents=True, exist_ok=True)
    gds_out = out_dir / f"{cell}.gds"
    clean_gds(gds, gds_out, cell)
    fill_macro(gds_out, cell)
    spice_out = out_dir / f"{cell}.spice"
    spice_out.write_text(ref_text, encoding="utf-8")

    lef_out = write_lef(gds_out, cell, [
        *port_lines(sig_pins, signals, ua),
        f"port {tcl_tok(vdd)} use power", f"port {tcl_tok(vdd)} class inout",
        f"port {tcl_tok(vss)} use ground", f"port {tcl_tok(vss)} class inout"],
        {**{p: False for p in [*sig_pins, *ua]}, vdd: True, vss: True})

    def rng(s: str) -> str:
        w = int(widths.get(s, 1))
        return f"[{w - 1}:0] " if w > 1 else ""
    sig_decls = [f"    {'output' if d == 'a2d' else 'input '} wire {rng(s)}{s}"
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


# gf180mcuD's routing stack in GDS layer numbers: each metal (its labels on
# datatype 10) and the via up from it.
METALS = {"Metal1": 34, "Metal2": 36, "Metal3": 42, "Metal4": 46,
          "Metal5": 81}
VIAS_UP = {"Metal1": 35, "Metal2": 38, "Metal3": 40, "Metal4": 41}
LABEL_DATATYPE = 10
# gf180's narrowest metal (Metal1, 0.23 um): a pin narrower than this is
# magic's 10 nm label point, not metal a router can land on.
MIN_PIN_UM = 0.23


def pin_lines(gds: Path, cell: str, ports: dict[str, bool]) -> list[str]:
    """The magic Tcl lines, run after `port makeall`, that make `lef write
    -pinonly` give each port in `ports` ({name: is a supply}) a pin a router
    can land on and offer nothing else. -pinonly writes a port's label as
    its pin and the rest of its net as OBS, and the generators' labels are
    GDS points, so each port's label is grown to the rectangle of its own
    layer's metal under it. A label `ports` does not name is deleted, so its
    net goes out as OBS. A supply also gets a port label on each Metal3
    rectangle of its net, where macro_pdn.tcl joins the tile's stripes.
    Refuses a port label that sits on no metal of its layer."""
    import klayout.db as db
    layout = db.Layout()
    layout.read(str(gds))
    top = layout.cell(cell)

    def region(layer: int, datatype: int = 0):
        li = layout.find_layer(layer, datatype)
        return (db.Region() if li is None else
                db.Region(top.begin_shapes_rec(li)).merged())

    def rects(reg) -> list:
        return [t.bbox() for poly in reg.each()
                for t in poly.decompose_trapezoids(db.Polygon.TD_simple)
                if t.is_box()]

    def um(b) -> str:
        return " ".join(f"{v * layout.dbu:.3f}um"
                        for v in (b.left, b.bottom, b.right, b.top))

    metal = {m: region(n) for m, n in METALS.items()}
    via_up = {m: region(n) for m, n in VIAS_UP.items()}
    names = list(METALS)

    def net_metal3(layer: str, start) -> list:
        net = {m: start if m == layer else db.Region() for m in names}
        while True:
            grown = dict(net)
            for lo, hi in zip(names, names[1:]):
                grown[hi] = grown[hi] + metal[hi].interacting(
                    via_up[lo].interacting(net[lo]))
                grown[lo] = grown[lo] + metal[lo].interacting(
                    via_up[lo].interacting(net[hi]))
            grown = {m: r.merged() for m, r in grown.items()}
            if all(grown[m].count() == net[m].count() for m in names):
                return rects(net["Metal3"])
            net = grown

    wanted = {p.lower(): p for p in ports}
    lines, supply_rects = [], {}
    for layer, gds_layer in METALS.items():
        li = layout.find_layer(gds_layer, LABEL_DATATYPE)
        texts = [] if li is None else [
            s.text for s in top.shapes(li).each() if s.is_text()]
        for text in texts:
            at = db.Box(text.x - 5, text.y - 5, text.x + 5, text.y + 5)
            lines += ["select clear", f"box {um(at)}", "select area labels"]
            pin = wanted.get(text.string.lower())
            if pin is None:
                lines.append("delete")
                continue
            shape = metal[layer].interacting(db.Region(at))
            under = [r for r in rects(shape)
                     if r.contains(db.Point(text.x, text.y))]
            if not under:
                x, y = text.x * layout.dbu, text.y * layout.dbu
                raise CheckError(
                    f"{gds.name}: the label {pin} at ({x:.3f}, {y:.3f}) "
                    f"sits on no {layer}, so its "
                    f"pin would be a 10 nm point; put it on the {layer} the "
                    "pin is")
            pin_box = max(under, key=lambda r: r.area())
            lines.append(f"setlabel box {um(pin_box)}")
            if ports[pin]:
                supply_rects.setdefault(pin, set()).update(
                    r for r in net_metal3(layer, shape) if r != pin_box)
    for pin, boxes in sorted(supply_rects.items()):
        for box in sorted(boxes, key=lambda b: (b.left, b.bottom)):
            lines += ["select clear", f"box {um(box)}",
                      f"label {tcl_tok(pin)} c metal3",
                      f"port make [port {tcl_tok(pin)} index]"]
    return lines + ["select clear"]


def lef_pins_too_small(lef: Path, pins: list[str]) -> list[str]:
    """The pins in `pins` the LEF gives no rectangle at least MIN_PIN_UM
    on each side - none at all, or only magic's 10 nm label point."""
    text = lef.read_text(encoding="utf-8", errors="replace")
    bad = []
    for pin in pins:
        m = re.search(rf"^\s*PIN\s+{re.escape(pin)}\s*$(.*?)^\s*END\s+"
                      rf"{re.escape(pin)}\s*$", text,
                      re.MULTILINE | re.DOTALL | re.IGNORECASE)
        sides = [(float(x1) - float(x0), float(y1) - float(y0))
                 for x0, y0, x1, y1 in re.findall(
                     r"RECT\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*;",
                     m.group(1) if m else "")]
        if not any(min(s) >= MIN_PIN_UM - 1e-6 for s in sides):
            bad.append(pin)
    return bad


def write_lef(gds_out: Path, cell: str, ports: list[str],
              pins: dict[str, bool]) -> Path:
    """magic's LEF abstract of `cell` in gds_out, written beside it;
    ports are the magic lines that set each pin's use and class, pins
    {name: is a supply} the cell's ports. Each pin is the metal under its
    label (pin_lines) and everything else the cell draws is OBS, so a
    router lands only where a port's label says. The abstract covers what
    the cell draws: refuses when it comes out more than 1 um short of that,
    or when a pin comes out with no rectangle a router can land on. The
    cell must draw from (0, 0) (clean_gds): a LEF ORIGIN other than 0 0 is
    refused, since the final GDS places the cell's origin, not the
    abstract's lower-left, where OpenROAD routed to it."""
    lef_out = gds_out.with_suffix(".lef")
    layoutlib.fresh(lef_out)
    script = "\n".join([
        "drc off", "crashbackups disable", "locking disable",
        f"gds read {gds_out.name}", f"load {cell}", "select top cell",
        # gds read turns the PR-boundary layer into FIXED_BBOX, and lef
        # write takes that as SIZE. A layout that flattens standard cells
        # carries only their boundaries, so the abstract shrank to those
        # cells and LibreLane placed the rest of the drawn macro over the
        # tile's standard-cell rows (PDN-0179, "Unable to repair all
        # channels"). Cleared, the abstract is everything magic reads.
        "property FIXED_BBOX {}",
        "port makeall", *pin_lines(gds_out, cell, pins), *ports,
        # plain `lef write` made each port's whole net its pin, so the
        # router dropped vias onto internal metal beside geometry the OBS
        # never named (ece298a's R-2R DAC, 23 DRC and 73 LVS findings)
        "property LEFclass BLOCK", f"lef write {lef_out.name} -pinonly",
        "quit -noprompt", ""])
    proc = layoutlib.run_eda(["magic", "-noconsole", "-dnull"],
                             cwd=gds_out.parent, timeout=300,
                             stdin_text=script)
    layoutlib.require_ok(proc, "magic lef write")
    if not lef_out.is_file():
        raise CheckError(f"magic wrote no {lef_out.name}: "
                         f"{(proc.stdout or '')[-1500:]}")
    size, drawn = lef_size(lef_out), gds_extent(gds_out, cell)
    if any(a < b - 1.0 for a, b in zip(size, drawn)):
        raise CheckError(f"magic's abstract {lef_out.name} (SIZE {size}) is "
                         f"smaller than what {gds_out.name} draws ({drawn}); "
                         "LibreLane would place the macro's geometry off its "
                         "abstract")
    origin = lef_origin(lef_out)
    if any(abs(v) > 0.0005 for v in origin):
        raise CheckError(f"magic's abstract {lef_out.name} has ORIGIN "
                         f"{origin[0]:.3f} {origin[1]:.3f}, so the routes "
                         f"OpenROAD draws to its pins would miss {cell}'s "
                         "pins in the final GDS; move what the cell draws "
                         "to start at (0, 0) (clean_gds does this for the "
                         "assembled top)")
    small = lef_pins_too_small(lef_out, list(pins))
    if small:
        raise CheckError(f"magic's abstract {lef_out.name} gives {small} no "
                         f"pin at least {MIN_PIN_UM} um on each side; label "
                         "each port on the metal the router should reach")
    return lef_out


def lef_pin_layers(lef: Path, pin: str) -> set[str]:
    """The layers a LEF pin's PORT shapes are on."""
    text = lef.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^\s*PIN\s+{re.escape(pin)}\s*$(.*?)^\s*END\s+"
                  rf"{re.escape(pin)}\s*$", text, re.MULTILINE | re.DOTALL)
    return set(re.findall(r"^\s*LAYER\s+(\S+)\s*;", m.group(1),
                          re.MULTILINE)) if m else set()


def unpowered_supplies(macro: dict) -> list[str]:
    """The macro's supply pins with no Metal3 shape. macro_pdn.tcl connects
    the tile's Metal4 stripes to the macro on Metal3 and nowhere else, so
    such a pin can never join the grid (PDN-0232, then PDN-0233)."""
    lef = Path(macro["files"]["lef"])
    return [pin for pin in (macro["power"]["vdd"], macro["power"]["vss"])
            if MACRO_POWER_LAYER not in lef_pin_layers(lef, pin)]


def lef_size(lef: Path) -> tuple[float, float]:
    m = re.search(r"^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", lef.read_text(),
                  re.MULTILINE)
    if not m:
        raise CheckError(f"{lef} carries no SIZE")
    return float(m.group(1)), float(m.group(2))


def lef_origin(lef: Path) -> tuple[float, float]:
    """The LEF macro's ORIGIN; (0, 0) when it gives none."""
    m = re.search(r"^\s*ORIGIN\s+(\S+)\s+(\S+)\s*;", lef.read_text(),
                  re.MULTILINE)
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


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
    A macro larger than the tile comes back with location None, and a ua
    map off the template with `ua_problems`, both with no spec.yaml written
    - design findings, not refusals."""
    digital = nested(ws, "digital", "vde")
    analog = nested(ws, "analog", "ade")
    iface = _yaml(ws / "interface.yaml", "interface.yaml")
    signals = {s["name"].lower(): s["direction"]
               for s in iface.get("signals") or []}
    if not signals:
        raise CheckError("interface.yaml names no signals")
    widths = {s["name"].lower(): int(s.get("width", 1))
              for s in iface.get("signals") or []}
    ua = {str(p).lower(): k for p, k in (iface.get("ua_pins") or {}).items()}

    dspec = _yaml(digital / "spec" / "spec.yaml", "digital spec.yaml")
    ports = {p.lower(): p for p in dspec.get("ports") or {}}
    missing = sorted(set(signals) - set(ports))
    if missing:
        raise CheckError(f"interface.yaml names {missing}, which the digital "
                         "spec.yaml's ports do not have")
    bad_width = sorted(
        s for s in signals
        if widths[s] != int(dspec["ports"][ports[s]].get("width", 1)))
    if bad_width:
        details = ", ".join(
            f"{s}: interface.yaml says {widths[s]}, spec.yaml's "
            f"{ports[s]} says {int(dspec['ports'][ports[s]].get('width', 1))}"
            for s in bad_width)
        raise CheckError(
            "interface.yaml's width does not match the digital spec.yaml "
            f"port's own width for {bad_width} ({details}); fix one of the "
            "two - a silent mismatch would pad or truncate the signal in "
            "the assembled top Verilog")

    top = ws / TOP_DIR
    if top.exists():
        shutil.rmtree(top)
    (top / "spec").mkdir(parents=True)
    shutil.copytree(digital / "rtl", top / "rtl")

    macro = analog_macro(analog, signals, top / "macros", ua, widths)
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
    # a ua map off the analog template is a design finding like a macro too
    # large (run() reports it), not a refusal
    tspec["ua_problems"] = ttlib.validate_ua_pins(tspec, tiles)
    if tspec["ua_problems"]:
        return top, tspec
    del tspec["ua_problems"]
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
                                 "pins", "ua") if k in m}
              for m in spec["macros"]]
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
    if spec.get("ua_problems"):
        violations = [checklib.violation(
            "top_harden", "error", None, "ua_pins", "ua_pin_off_template", [],
            f"interface.yaml ua_pins do not fit the analog tile: {p}", SCRIPT)
            for p in spec["ua_problems"]]
        payload = checklib.report(SCRIPT, ws / "digital" / "rtl", violations,
                                  macros=macros)
        return payload, args.out
    unpowered = [(m, pin) for m in spec["macros"]
                 for pin in unpowered_supplies(m)]
    if unpowered:
        violations = [checklib.violation(
            "top_harden", "error", None, m["cell"], "macro_power_unreachable",
            [], f"the analog macro {m['cell']}'s supply pin {pin} has no "
            f"{MACRO_POWER_LAYER} shape, and the tile's power stripes "
            f"(Metal4) join a macro only on {MACRO_POWER_LAYER}; draw {pin} "
            f"as a {MACRO_POWER_LAYER} strap across the cell's width, as "
            "corpus/msde/dac_tile's generator does", SCRIPT)
            for m, pin in unpowered]
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
