"""ttlib.py - the Tiny Tapeout GF180 template: pin budget, wrapper
generation and the merged LibreLane config (docs/design.md 1.5/"### M4.").

New at M4 - nothing to port. `engine/reference/tt/` vendors the pieces
(VENDORED.md has the commits + licences); this module is the only code that
reads them. Two rules the design states directly:

  "Tile size and pin constraints come from the template, never typed by
  hand" (M4 boundaries) - `pin_budget()` gets the ui_in/uo_out/uio_*
  bus widths by PARSING the vendored `tt_block_1x1_pgvdd.def`'s own PINS
  section, and `tile_die_area()` reads the vendored `tile_sizes.yaml`;
  neither is a literal `8` or a `DIE_AREA` string typed anywhere in this
  file. `gf180_tech()` imports the vendored `tech.py` module directly and
  returns its `tech_map["gf180mcuD"]` object, so the PDK-specific LibreLane
  keys (LIB_SYNTH, STA_CORNERS, ...) come from that file too.

  "a script generates tt_um_<name>.v from tt_pins" (M4 boundaries) -
  `generate_tt_wrapper()`.

tt_pins schema (spec.yaml, docs/design.md 1.4's `tt_pins: {name: pin}`):
    tt_pins:
      <port-name>: "<expr>"
where <port-name> is a key of spec.yaml's `ports` map (or the two synthetic
ports every digital block has for free: the clock net and, when the design
declares a `rst` port, nothing special - the reset polarity is spelled out
in the expr itself) and <expr> is one of:
    clk | ena | rst_n | ~rst_n
    ui_in[<n>] | ui_in[<hi>:<lo>] | uio_in[<n>] | uio_in[<hi>:<lo>]
    uo_out[<n>] | uo_out[<hi>:<lo>] | uio_out[<n>] | uio_out[<hi>:<lo>]
An input-direction port may use a leading `~` (the only transform this
generator supports - a reset polarity flip, the one real case in the
corpus); an output-direction port may not (you cannot inject a NOT gate
into an output net through a bare port connection the way you can invert a
bare input wire, and no corpus design has needed it). `uio_out`/`uio_oe`
are wired but never driven high by anything this module builds today - the
corpus uses `uio_in` only as extra input bits (9-bit UART data+start),
never as a true bidirectional pin; `uio_oe` is tied to all-zero.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

TT_DIR = Path(__file__).resolve().parents[1] / "reference" / "tt"
TEMPLATE_DIR = TT_DIR / "ttgf-verilog-template"
SUPPORT_DIR = TT_DIR / "tt-support-tools"
PRECHECK_DIR = SUPPORT_DIR / "precheck"
TEMPLATE_CONFIG = TEMPLATE_DIR / "src" / "config.json"
TEMPLATE_INFO_YAML = TEMPLATE_DIR / "info.yaml"
TECH_PY = SUPPORT_DIR / "tech.py"
TILE_SIZES_YAML = SUPPORT_DIR / "tech" / "gf180mcuD" / "tile_sizes.yaml"
DEF_DIR = SUPPORT_DIR / "tech" / "gf180mcuD" / "def"

PDK_NAME = "gf180mcuD"
DEFAULT_TILES = "1x1"

# name -> (is_input, needs a matching uio_oe bit forced high)
_BUS_KIND = {
    "ui_in": ("in", None),
    "uio_in": ("in", None),
    "uo_out": ("out", None),
    "uio_out": ("out", "uio_oe"),
}
# every bus the DEF template places pins for - one more than _BUS_KIND
# (uio_oe is never a tt_pins target, this module always drives it itself,
# but pin_budget() still needs its width to size that all-zero assign).
_ALL_BUSES = (*_BUS_KIND, "uio_oe")
_SCALAR_IN = {"clk", "ena", "rst_n"}

PIN_EXPR_RE = re.compile(
    r"\A(?P<inv>~)?(?P<base>clk|ena|rst_n|ui_in|uo_out|uio_in|uio_out)"
    r"(?:\[(?P<hi>\d+)(?::(?P<lo>\d+))?\])?\Z")


class TTError(RuntimeError):
    """A tt_pins mapping, or the vendored template itself, is unusable."""


def def_template_path(tiles: str = DEFAULT_TILES) -> Path:
    return DEF_DIR / f"tt_block_{tiles}_pgvdd.def"


def pin_budget(tiles: str = DEFAULT_TILES) -> dict[str, int]:
    """{'ui_in': 8, 'uo_out': 8, 'uio_in': 8, 'uio_out': 8} - the bus widths
    the vendored DEF template's own PINS section declares (its highest
    `<bus>[N]` index + 1 each), never a hardcoded 8. Raises TTError if the
    template is missing or declares no bits for a bus this module wires."""
    path = def_template_path(tiles)
    if not path.is_file():
        raise TTError(f"no vendored DEF template at {path} (tiles={tiles!r})")
    text = path.read_text(encoding="utf-8")
    widths: dict[str, int] = {}
    for bus in _ALL_BUSES:
        idxs = [int(m.group(1)) for m in
                re.finditer(rf"-\s+{bus}\[(\d+)\]\s+\+\s+NET", text)]
        if not idxs:
            raise TTError(f"{path}: no {bus}[N] pins found")
        widths[bus] = max(idxs) + 1
    return widths


def tile_die_area(tiles: str = DEFAULT_TILES) -> str:
    if not TILE_SIZES_YAML.is_file():
        raise TTError(f"no vendored tile_sizes.yaml at {TILE_SIZES_YAML}")
    import yaml
    data = yaml.safe_load(TILE_SIZES_YAML.read_text(encoding="utf-8"))
    if tiles not in data:
        raise TTError(f"{TILE_SIZES_YAML}: no entry for tiles={tiles!r} "
                      f"(known: {sorted(data)})")
    return data[tiles]


def load_template_config() -> dict:
    """The vendored `src/config.json`, comments stripped the same way
    tt-support-tools' own config_utils.read_json_config does (a literal
    `"//"` key, last-one-wins under json.loads - never a value this code
    tries to interpret)."""
    if not TEMPLATE_CONFIG.is_file():
        raise TTError(f"no vendored template config at {TEMPLATE_CONFIG}")
    data = json.loads(TEMPLATE_CONFIG.read_text(encoding="utf-8"))
    data.pop("//", None)
    return data


_TECH_MODULE = None


def gf180_tech():
    """`tech_map["gf180mcuD"]` from the vendored `tech.py`, imported by
    file path (never installed as a package - it is vendored, not a
    dependency) and cached for the process."""
    global _TECH_MODULE
    if _TECH_MODULE is None:
        if not TECH_PY.is_file():
            raise TTError(f"no vendored tech.py at {TECH_PY}")
        spec = importlib.util.spec_from_file_location("chip_flow_vendored_tt_tech",
                                                       TECH_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TECH_MODULE = mod
    return _TECH_MODULE.tech_map[PDK_NAME]


def parse_pin_expr(expr: str) -> dict:
    """{'invert': bool, 'base': 'ui_in'|'clk'|..., 'hi': int|None,
    'lo': int|None}. Raises TTError on anything not matching the grammar
    this docstring's module-level comment spells out."""
    if not isinstance(expr, str):
        raise TTError(f"tt_pins expression must be a string, got {expr!r}")
    m = PIN_EXPR_RE.match(expr.strip())
    if not m:
        raise TTError(f"tt_pins expression {expr!r} does not match the "
                      "grammar (clk|ena|rst_n|ui_in[..]|uo_out[..]|"
                      "uio_in[..]|uio_out[..], optionally ~-prefixed)")
    base = m.group("base")
    invert = bool(m.group("inv"))
    if base in _SCALAR_IN and m.group("hi") is not None:
        raise TTError(f"tt_pins expression {expr!r}: {base!r} takes no bit index")
    if base not in _SCALAR_IN and m.group("hi") is None:
        raise TTError(f"tt_pins expression {expr!r}: {base!r} needs a bit "
                      "index or range")
    hi = int(m.group("hi")) if m.group("hi") is not None else None
    lo = int(m.group("lo")) if m.group("lo") is not None else hi
    if hi is not None and lo is not None and lo > hi:
        raise TTError(f"tt_pins expression {expr!r}: lo > hi")
    return {"invert": invert, "base": base, "hi": hi, "lo": lo}


def validate_tt_pins(spec: dict, tiles: str = DEFAULT_TILES) -> list[str]:
    """Plain-English problem strings (empty = clean) - "tt_pins fit the
    tile" (gates.yaml's spec_lint description): every spec.yaml port is
    mapped, every mapping matches the port's own direction and width, an
    input mapping may invert and an output mapping may not, and no two
    ports claim the same physical bit."""
    problems: list[str] = []
    ports = spec.get("ports") or {}
    tt_pins = spec.get("tt_pins") or {}
    if not tt_pins:
        return ["spec.yaml has no non-empty 'tt_pins' mapping"]
    budget = pin_budget(tiles)
    claimed: dict[str, set[int]] = {bus: set() for bus in _BUS_KIND}
    for name, port in ports.items():
        if name not in tt_pins:
            problems.append(f"port {name!r} has no tt_pins mapping")
            continue
        try:
            parsed = parse_pin_expr(tt_pins[name])
        except TTError as exc:
            problems.append(str(exc))
            continue
        width = int(port.get("width", 1))
        pin_width = (1 if parsed["hi"] is None
                    else parsed["hi"] - parsed["lo"] + 1)
        if pin_width != width:
            problems.append(f"port {name!r} is {width} bit(s) wide but "
                            f"tt_pins maps it to {pin_width} bit(s) "
                            f"({tt_pins[name]!r})")
        base = parsed["base"]
        direction = port.get("dir")
        if base in _SCALAR_IN:
            if direction != "input":
                problems.append(f"port {name!r} is {direction!r} but maps "
                                f"to {base!r}, an input-only TT pin")
            continue
        kind, _ = _BUS_KIND[base]
        if kind == "in" and direction != "input":
            problems.append(f"port {name!r} is {direction!r} but maps to "
                            f"{base}, an input bus")
        if kind == "out":
            if direction != "output":
                problems.append(f"port {name!r} is {direction!r} but maps "
                                f"to {base}, an output bus")
            if parsed["invert"]:
                problems.append(f"port {name!r}: an output mapping "
                                "('{}') cannot be inverted".format(
                                    tt_pins[name]))
        n_bits = budget.get(base, 0)
        hi = parsed["hi"] if parsed["hi"] is not None else 0
        lo = parsed["lo"] if parsed["lo"] is not None else 0
        if hi >= n_bits or lo < 0:
            problems.append(f"port {name!r} maps to {base}[{hi}:{lo}] but "
                            f"the tile only has {base}[{n_bits - 1}:0]")
            continue
        overlap = claimed[base] & set(range(lo, hi + 1))
        if overlap:
            problems.append(f"port {name!r} claims {base}{sorted(overlap)} "
                            "already used by another port")
        claimed[base].update(range(lo, hi + 1))
    extra = set(tt_pins) - set(ports)
    if extra:
        problems.append(f"tt_pins names ports not in spec.yaml 'ports': "
                        f"{sorted(extra)}")
    return problems


def wrapper_name(spec: dict) -> str:
    top = spec.get("top") or ""
    return top if top.startswith("tt_um_") else f"tt_um_{top}"


def generate_tt_wrapper(spec: dict, tiles: str = DEFAULT_TILES) -> str:
    """The Verilog source of `tt_um_<top>.v`: the fixed TT interface,
    instantiating spec['top'] with every port wired per tt_pins. Raises
    TTError (never emits invalid Verilog) when validate_tt_pins() would
    report a problem."""
    problems = validate_tt_pins(spec, tiles)
    if problems:
        raise TTError("tt_pins does not fit the tile: " + "; ".join(problems))
    top = spec["top"]
    wname = wrapper_name(spec)
    ports = spec.get("ports") or {}
    tt_pins = spec["tt_pins"]

    conns: list[str] = []
    in_assigns: list[str] = []
    out_bits: dict[str, dict[int, str]] = {"uo_out": {}, "uio_out": {}}
    used_ui_bits: set[int] = set()
    used_uio_in_bits: set[int] = set()

    for name, port in sorted(ports.items()):
        parsed = parse_pin_expr(tt_pins[name])
        width = int(port.get("width", 1))
        wire = f"w_{name}"
        conns.append(f"      .{name}({wire})")
        if port.get("dir") == "input":
            src = parsed["base"]
            if src in _SCALAR_IN:
                expr = src
            else:
                idx = (f"[{parsed['hi']}:{parsed['lo']}]"
                      if parsed["hi"] != parsed["lo"] else f"[{parsed['hi']}]")
                expr = f"{src}{idx}"
                bits = range(parsed["lo"], parsed["hi"] + 1)
                (used_ui_bits if src == "ui_in" else used_uio_in_bits).update(bits)
            if parsed["invert"]:
                expr = f"~{expr}"
            in_assigns.append(f"  wire [{width - 1}:0] {wire};\n"
                              f"  assign {wire} = {expr};" if width > 1 else
                              f"  wire {wire};\n  assign {wire} = {expr};")
        else:
            in_assigns.append(f"  wire [{width - 1}:0] {wire};" if width > 1
                              else f"  wire {wire};")
            bus = parsed["base"]
            lo = parsed["lo"]
            for bit in range(width):
                bit_expr = wire if width == 1 else f"{wire}[{bit}]"
                out_bits[bus][lo + bit] = bit_expr

    def bus_expr(bus: str, n_bits: int) -> str:
        pieces = [out_bits[bus].get(b, "1'b0") for b in reversed(range(n_bits))]
        return "{" + ", ".join(pieces) + "}"

    budget = pin_budget(tiles)
    unused_ui = [f"ui_in[{b}]" for b in range(budget["ui_in"])
                if b not in used_ui_bits]
    unused_uio_in = [f"uio_in[{b}]" for b in range(budget["uio_in"])
                    if b not in used_uio_in_bits]
    unused = ["ena", *unused_ui, *unused_uio_in]
    # uio_oe: high for every uio_out bit a port actually drives (TT: 0 =
    # input, 1 = output on that pin) - low, otherwise every uio_out[n] this
    # module writes would be silently ignored downstream (the pin stays
    # configured as an input no matter what this module drives it to).
    uio_oe_bits = "".join("1" if b in out_bits["uio_out"] else "0"
                          for b in reversed(range(budget["uio_oe"])))

    lines = [
        "/* generated by engine/lib/ttlib.py::generate_tt_wrapper - do not "
        "hand-edit; regenerate from spec.yaml's tt_pins instead. */",
        "`default_nettype none",
        "",
        f"module {wname} (",
        "    input  wire [7:0] ui_in,",
        "    output wire [7:0] uo_out,",
        "    input  wire [7:0] uio_in,",
        "    output wire [7:0] uio_out,",
        "    output wire [7:0] uio_oe,",
        "    input  wire       ena,",
        "    input  wire       clk,",
        "    input  wire       rst_n",
        ");",
        *in_assigns,
        "",
        f"  {top} u_{top} (",
        *([",\n".join(conns)] if conns else []),
        "  );",
        "",
        f"  assign uo_out = {bus_expr('uo_out', budget['uo_out'])};",
        f"  assign uio_out = {bus_expr('uio_out', budget['uio_out'])};",
        f"  assign uio_oe = {budget['uio_oe']}'b{uio_oe_bits};",
        f"  wire _unused = &{{{', '.join(unused)}, 1'b0}};",
        "endmodule",
        # see generate_glsim_harness's matching comment: `default_nettype
        # none is a standing directive across every file one Icarus run
        # compiles, not scoped to this one - reset it before this file is
        # ever compiled alongside PDK verilog that relies on implicit nets.
        "`default_nettype wire",
        "",
    ]
    return "\n".join(lines)


def generate_glsim_harness(spec: dict, dut_instance: str = "dut",
                           sdf_path: Path | str | None = None,
                           tiles: str = DEFAULT_TILES) -> str:
    """The `glsim` gate's own harness (docs/design.md 1.5's `glsim` row): the
    INVERSE of generate_tt_wrapper - a module named exactly `spec['top']`,
    with `spec['ports']`'s own port list (so tb/*.py, written against the
    original RTL, drives and observes it unmodified), instantiating the
    gate-level netlist's top module (`wrapper_name(spec)`) through the same
    tt_pins map read the other way: an original INPUT port drives its TT
    pin(s) (inverted where tt_pins says `~`), an original OUTPUT port is
    read from its TT pin(s) directly. `ena` is tied high (the design is
    always "powered"). `sdf_path`, when given, adds the `$sdf_annotate`
    initial block the SDF-backannotated pass of `glsim` needs, referencing
    `dut_instance` by its LOCAL name - the netlist's own internal instance
    paths inside the SDF are unaffected by whatever this harness is called."""
    problems = validate_tt_pins(spec, tiles)
    if problems:
        raise TTError("tt_pins does not fit the tile: " + "; ".join(problems))
    top = spec["top"]
    dut_module = wrapper_name(spec)
    ports = spec.get("ports") or {}
    tt_pins = spec["tt_pins"]
    budget = pin_budget(tiles)

    port_decls: list[str] = []
    body: list[str] = []
    in_bits: dict[str, dict[int, str]] = {"ui_in": {}, "uio_in": {}}
    scalar_assigns: dict[str, str] = {}
    out_reads: list[str] = []

    for name, port in sorted(ports.items()):
        parsed = parse_pin_expr(tt_pins[name])
        width = int(port.get("width", 1))
        direction = port.get("dir")
        vtype = "wire" if width == 1 else f"wire [{width - 1}:0]"
        if direction == "input":
            port_decls.append(f"    input  {vtype} {name}")
            base = parsed["base"]
            if base in _SCALAR_IN:
                expr = f"~{name}" if parsed["invert"] else name
                scalar_assigns[base] = expr
            else:
                for bit in range(width):
                    src = name if width == 1 else f"{name}[{bit}]"
                    if parsed["invert"]:
                        src = f"~{src}"
                    in_bits[base][parsed["lo"] + bit] = src
        else:
            port_decls.append(f"    output {vtype} {name}")
            base = parsed["base"]
            idx = (f"[{parsed['hi']}:{parsed['lo']}]"
                  if parsed["hi"] != parsed["lo"] else f"[{parsed['hi']}]")
            out_reads.append(f"  assign {name} = tt_{base}{idx};")

    def bus_expr(bus: str) -> str:
        n_bits = budget[bus]
        bits = in_bits[bus]
        return "{" + ", ".join(bits.get(b, "1'b0") for b in reversed(range(n_bits))) + "}"

    # every DUT-side net is `tt_`-prefixed: spec.yaml's own port names are
    # arbitrary (a design can - and counter8 does - call a port `clk`), so
    # an unprefixed local alias for the TT pin of the same name would be a
    # self-referential `wire clk = clk;` the moment tt_pins maps a port
    # straight through.
    body.append(f"  wire [{budget['ui_in'] - 1}:0] tt_ui_in = {bus_expr('ui_in')};")
    body.append(f"  wire [{budget['uio_in'] - 1}:0] tt_uio_in = {bus_expr('uio_in')};")
    body.append(f"  wire [{budget['uo_out'] - 1}:0] tt_uo_out;")
    body.append(f"  wire [{budget['uio_out'] - 1}:0] tt_uio_out;")
    body.append(f"  wire [{budget['uio_oe'] - 1}:0] tt_uio_oe;")
    body.append("  wire tt_ena = 1'b1;")
    clk_expr = scalar_assigns.get("clk", "1'b0")
    rst_n_expr = scalar_assigns.get("rst_n", "1'b1")
    body.append(f"  wire tt_clk = {clk_expr};")
    body.append(f"  wire tt_rst_n = {rst_n_expr};")
    body.extend(out_reads)
    body.append("")
    body.append(f"  {dut_module} {dut_instance} (")
    body.append("      .ui_in(tt_ui_in), .uo_out(tt_uo_out), .uio_in(tt_uio_in),")
    body.append("      .uio_out(tt_uio_out), .uio_oe(tt_uio_oe), .ena(tt_ena),")
    body.append("      .clk(tt_clk), .rst_n(tt_rst_n)")
    body.append("  );")
    if sdf_path is not None:
        body.append(f'  initial $sdf_annotate("{sdf_path}", {dut_instance});')

    lines = [
        "/* generated by engine/lib/ttlib.py::generate_glsim_harness - do "
        "not hand-edit. */",
        "`default_nettype none",
        "",
        f"module {top} (",
        ",\n".join(port_decls),
        ");",
        *body,
        "endmodule",
        # restore the DEFAULT default_nettype before whatever else this
        # source file is compiled alongside: `default_nettype none is a
        # standing compiler-directive state in Icarus's one-pass elaboration
        # of every file in a run, not scoped to this file - left at `none`,
        # the GF180 cell library's own gate-level models (which rely on
        # Verilog's implicit net declaration for internal nets, e.g.
        # `not g(MGM_D0, D);` with no prior `wire MGM_D0;`) fail to elaborate
        # ("Net MGM_D0 is not defined in this context") the moment they are
        # compiled after this file, exactly the case check_glsim.py needs
        # (this harness plus the PDK's own verilog in one iverilog run).
        "`default_nettype wire",
        "",
    ]
    return "\n".join(lines)


def harden_config(spec: dict, rtl_files: list[Path], wrapper_path: Path,
                  pdk_root: Path, tiles: str = DEFAULT_TILES) -> dict:
    """The merged LibreLane config.json: the vendored template's own
    defaults, overlaid with this design's DESIGN_NAME/VERILOG_FILES/
    DIE_AREA/FP_DEF_TEMPLATE/clock (docs/design.md 1.5's harden row) and the
    vendored tech.py's gf180mcuD-specific keys (LIB_SYNTH, STA_CORNERS,
    ...) - the same three layers project.py's own create_user_config()/
    golden_harden() apply, read from the files this module vendors rather
    than retyped."""
    config = load_template_config()
    tech = gf180_tech()
    clock = spec.get("clock") or {}
    period = clock.get("period_ns")
    config.update({
        "DESIGN_NAME": wrapper_name(spec),
        "VERILOG_FILES": [str(p) for p in [*rtl_files, wrapper_path]],
        "DIE_AREA": tile_die_area(tiles),
        "FP_DEF_TEMPLATE": str(def_template_path(tiles)),
        "VDD_PIN": "VPWR",
        "GND_PIN": "VGND",
        "RT_MAX_LAYER": tech.project_top_metal_layer,
        "PDK_ROOT": str(pdk_root),
    })
    if isinstance(period, (int, float)):
        config["CLOCK_PERIOD"] = float(period)
    config.update(tech.librelane_config)
    return config


def stdcell_liberty_path(corner: str, pdk_root: Path) -> Path:
    """The PDK's own standard-cell liberty for one of LibreLane's corner
    names (e.g. 'max_ss_125C_3v00'), resolved from the vendored tech.py's
    own `librelane_config["LIB"]` map - never a second, hand-typed copy of
    those filenames. LibreLane's corner names are `<bucket>_<pvt>` (bucket
    in nom/min/max, a SPEF grouping only - the same three .lib files serve
    every bucket); `LIB` keys on `*_<pvt>` for exactly that reason.

    This is deliberately NOT `final/lib/<corner>/tt_um_<top>__<corner>.lib`
    - that file is the HARDENED MACRO's own abstracted timing model (what a
    parent design would read to use this block as an IP macro), not the
    standard-cell library the gate-level netlist inside it needs linked."""
    tech = gf180_tech()
    _, _, pvt = corner.partition("_")
    entries = tech.librelane_config.get("LIB", {}).get(f"*_{pvt}")
    if not entries:
        raise TTError(f"no LIB entry for corner {corner!r} (pvt {pvt!r}) "
                      "in the vendored tech.py's librelane_config")
    value = entries[0]
    prefix = "pdk_dir::"
    if not value.startswith(prefix):
        raise TTError(f"unexpected LIB path shape (no {prefix!r} prefix): {value!r}")
    return Path(pdk_root) / PDK_NAME / value[len(prefix):]


def write_info_yaml(spec: dict, dest: Path, tiles: str = DEFAULT_TILES) -> None:
    """A minimal info.yaml at the workspace's harden/ root - precheck.py
    walks up from the GDS looking for exactly this file (main(): `while not
    os.path.exists(f"{yaml_dir}/info.yaml")`), and reads `top_module`/
    `tiles` from it, nothing else this corpus needs."""
    import yaml
    data = {"project": {"title": spec.get("top", ""), "top_module":
                        wrapper_name(spec), "tiles": tiles},
            "pinout": {}}
    dest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# precheck's own missing python dependency (VENDORED.md: "the one
# integration point, not a source edit")
# ---------------------------------------------------------------------------
def _pydeps_cache_root() -> Path:
    import os
    if os.environ.get("CC_WORKER_SANDBOX") == "1" or \
            os.environ.get("CC_MEMBER_SANDBOX") == "1":
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "chip-flow" / "tt-precheck-pydeps"


def ensure_precheck_deps(eda_bin: Path) -> Path:
    """Return a directory holding `gdstk` (and anything else precheck.py
    imports that the image does not carry), installing it on first use with
    `eda python3 -m pip install --target` - never under
    ~/.cc/toolchains (CLAUDE.md: it is a read-only image; every write here
    goes to the same sandbox-aware cache root bin/eda's own shim cache
    uses, checked with realpath before anything is written to it)."""
    cache = _pydeps_cache_root()
    marker = cache / ".ok"
    real = Path(cache).resolve() if cache.exists() else cache
    if str(TT_DIR.resolve()) in str(real):
        raise TTError(f"refusing to write pydeps cache under vendored tt/: {real}")
    if marker.is_file():
        return cache
    cache.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(eda_bin), "python3", "-m", "pip", "install", "--no-input",
         "--target", str(cache), "gdstk"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise TTError(f"could not install precheck's python deps into "
                      f"{cache}: {proc.stderr[-2000:]}")
    marker.write_text("ok\n", encoding="utf-8")
    return cache


def yowasp_yosys_shim_dir(eda_bin: Path, cache: Path) -> Path:
    """A directory holding a `yowasp-yosys` shim (VENDORED.md) that re-execs
    `eda_bin yosys` - written into the same cache ensure_precheck_deps()
    returns, so both live under one sandbox-aware, non-toolchain root."""
    shim_dir = cache / "bin"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / "yowasp-yosys"
    import shlex
    body = f"#!/bin/sh\nexec {shlex.quote(str(eda_bin))} yosys \"$@\"\n"
    shim.write_text(body, encoding="utf-8")
    shim.chmod(0o755)
    return shim_dir
