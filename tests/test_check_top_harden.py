"""check_top_harden's macro sizing, without magic or LibreLane; and one
slow real run of the analog tile (corpus/msde/dac_tile)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1] / "engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))

import check_top_harden  # noqa: E402
from checklib import CheckError  # noqa: E402

db = pytest.importorskip("klayout.db")


def _gds(tmp_path: Path, width_um: float) -> Path:
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("mac")
    cell.shapes(layout.layer(34, 0)).insert(
        db.DBox(0, 0, width_um, 1))
    path = tmp_path / "mac.gds"
    layout.write(str(path))
    return path


def test_gds_extent_reads_drawn_geometry(tmp_path):
    assert check_top_harden.gds_extent(_gds(tmp_path, 400), "mac") == (400.0, 1.0)


def test_macro_size_takes_drawn_extent_past_lef(tmp_path):
    lef = tmp_path / "mac.lef"
    lef.write_text("MACRO mac\n  SIZE 45.600 BY 31.040 ;\nEND mac\n")
    # metal drawn past the LEF boundary widens the macro
    assert check_top_harden.macro_size(lef, _gds(tmp_path, 400), "mac") == (400.0, 31.04)
    # geometry inside the boundary leaves the LEF SIZE standing
    assert check_top_harden.macro_size(lef, _gds(tmp_path, 10), "mac") == (45.6, 31.04)


def test_signal_pins_expands_a_bus_to_its_bits():
    pins = check_top_harden.signal_pins({"dac_code": "d2a", "en": "d2a"},
                                        {"dac_code": 8})
    assert pins["en"] == "en"
    assert {p for p, s in pins.items() if s == "dac_code"} == {
        f"dac_code[{i}]" for i in range(8)}
    assert "dac_code" not in pins


def test_centre_fits_and_refuses():
    die = "0 0 346.64 160.72"
    assert check_top_harden.centre(die, 45.6, 31.04) == [150.64, 66.64]
    assert check_top_harden.centre(die, 400.0, 31.04) is None


def _lef(tmp_path: Path, vdd_layer: str, vss_layer: str) -> dict:
    lef = tmp_path / "mac.lef"
    lef.write_text("".join(
        f"  PIN {pin}\n    USE {use} ;\n    PORT\n      LAYER {layer} ;\n"
        f"        RECT 0 0 40 1 ;\n    END\n  END {pin}\n"
        for pin, use, layer in (("vdd", "POWER", vdd_layer),
                                ("vss", "GROUND", vss_layer))))
    return {"files": {"lef": str(lef)}, "power": {"vdd": "vdd", "vss": "vss"}}


def test_a_supply_with_no_metal3_is_unreachable(tmp_path):
    # the r2r DAC's shape: vdd on Metal2 only, which no stripe can land on
    assert check_top_harden.unpowered_supplies(
        _lef(tmp_path, "Metal2", "Metal3")) == ["vdd"]
    assert check_top_harden.unpowered_supplies(
        _lef(tmp_path, "Metal3", "Metal3")) == []


# ----------------------------------------------- interface.yaml vs. spec.yaml

def _iface_ws(tmp_path: Path, sig_width: int, port_width: int) -> Path:
    """A workspace with just enough for assemble() to reach the width
    check: digital/ and analog/ each carry state.json for nested(), and
    digital/spec/spec.yaml's one port's width may or may not match
    interface.yaml's own width for the same signal."""
    import yaml

    ws = tmp_path / "ws"
    (ws / "digital" / "spec").mkdir(parents=True)
    (ws / "digital" / "rtl").mkdir(parents=True)
    (ws / "analog").mkdir(parents=True)
    ws.joinpath("digital", "state.json").write_text(
        json.dumps({"skill": "vde"}), encoding="utf-8")
    ws.joinpath("analog", "state.json").write_text(
        json.dumps({"skill": "ade", "block": "dac"}), encoding="utf-8")
    ws.joinpath("digital", "spec", "spec.yaml").write_text(
        yaml.safe_dump({"ports": {
            "dac_code": {"dir": "input", "width": port_width}}}),
        encoding="utf-8")
    ws.joinpath("interface.yaml").write_text(
        yaml.safe_dump({"signals": [
            {"name": "dac_code", "direction": "d2a", "width": sig_width}]}),
        encoding="utf-8")
    return ws


def test_assemble_refuses_when_interface_width_does_not_match_spec_port(
        tmp_path):
    ws = _iface_ws(tmp_path, sig_width=8, port_width=4)
    with pytest.raises(CheckError, match="width does not match") as exc:
        check_top_harden.assemble(ws)
    assert "dac_code" in str(exc.value)


def test_assemble_passes_the_width_check_when_widths_match(tmp_path):
    ws = _iface_ws(tmp_path, sig_width=8, port_width=8)
    # past the width check, assemble() moves on to building the analog
    # macro and fails there instead (no generator under analog/) - proves
    # the width check itself does not fire on a genuine match
    with pytest.raises(CheckError, match="no layout generator"):
        check_top_harden.assemble(ws)


# ---------------------------------------------------- the magic Tcl script

def test_tcl_tok_braces_a_name():
    assert check_top_harden.tcl_tok("dac_code[0]") == "{dac_code[0]}"
    assert check_top_harden.tcl_tok("en") == "{en}"


def test_port_lines_braces_every_bus_bit_with_use_and_class():
    # magic reads this script as Tcl - an unbraced bus-bit name like
    # dac_code[0] runs [0] as a nested command, so a braced token is the
    # only way a bit's `use`/`class` lines ever reach the right port.
    sig_pins = check_top_harden.signal_pins(
        {"dac_code": "d2a", "vout": "a2d"}, {"dac_code": 4})
    lines = check_top_harden.port_lines(
        sig_pins, {"dac_code": "d2a", "vout": "a2d"}, {"trim": 2})
    for i in range(4):
        pin = f"dac_code[{i}]"
        assert f"port {{{pin}}} use signal" in lines
        assert f"port {{{pin}}} class input" in lines
    assert "port {vout} use signal" in lines
    assert "port {vout} class output" in lines
    # ua pads are inout, and braced the same way
    assert "port {trim} use signal" in lines
    assert "port {trim} class inout" in lines
    # every line is a single well-formed Tcl word list - no bare `[` or `]`
    # outside a brace pair
    for line in lines:
        assert re.search(r"\{[^{}]+\}", line), line


def _pinned_gds(tmp_path: Path, *, a_label_on_metal: bool = True) -> Path:
    """A cell like the r2r DAC's dac_code[i]: port a's label on a Metal2 pad
    at the left edge, whose Via1 drops onto a Metal1 strip running into the
    cell; an internal net labelled net1; vdd labelled on its Metal3 rail,
    vss on its Metal1 rail with a Via1/Via2 stack up to its Metal3 rail."""
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("mac")

    def box(layer, *b):
        cell.shapes(layout.layer(layer, 0)).insert(db.DBox(*b))

    def label(layer, text, x, y):
        cell.shapes(layout.layer(layer, 10)).insert(db.DText(text, x, y))
    box(36, 0, 10, 2, 11)                              # a's Metal2 pad
    label(36, "a", 0.5 if a_label_on_metal else 3.5, 10.5)
    box(35, 1.37, 10.37, 1.63, 10.63)                  # its Via1
    box(34, 1, 10, 40, 11)                             # a's internal strip
    box(34, 5, 12, 40, 13)
    label(34, "net1", 20, 12.5)                        # an internal net
    box(42, 0, 18, 40, 20)
    label(42, "vdd", 20, 19)
    box(34, 0, 0, 40, 1)
    label(34, "vss", 20, 0.5)
    box(35, 20, 0.3, 20.26, 0.56)
    box(36, 19.8, 0, 20.5, 1.5)
    box(38, 20, 1, 20.26, 1.26)
    box(42, 0, 0, 40, 2)                               # vss's Metal3 rail
    path = tmp_path / "mac.gds"
    layout.write(str(path))
    return path


PINNED = {"a": False, "vdd": True, "vss": True}


def test_pin_lines_grow_each_port_to_its_metal_and_drop_other_labels(
        tmp_path):
    lines = check_top_harden.pin_lines(_pinned_gds(tmp_path), "mac", PINNED)
    # a's pin is its Metal2 pad, not the Metal1 strip its net runs on
    assert "setlabel box 0.000um 10.000um 2.000um 11.000um" in lines
    assert not any("40.000um 11.000um" in line for line in lines)
    # vdd's label sits on its rail; vss's sits on Metal1 and gains the
    # Metal3 rail of its net as a second port label
    assert "setlabel box 0.000um 18.000um 40.000um 20.000um" in lines
    assert "setlabel box 0.000um 0.000um 40.000um 1.000um" in lines
    i = lines.index("box 0.000um 0.000um 40.000um 2.000um")
    assert lines[i + 1:i + 3] == ["label {vss} c metal3",
                                  "port make [port {vss} index]"]
    assert not any(line.startswith("label {vdd}") for line in lines)
    # net1 names no port: its label is deleted, the label after a port's
    # select is resized instead
    i = lines.index("box 19.995um 12.495um 20.005um 12.505um")
    assert lines[i + 1:i + 3] == ["select area labels", "delete"]
    assert lines.count("delete") == 1


def test_pin_lines_refuse_a_port_label_on_no_metal(tmp_path):
    gds = _pinned_gds(tmp_path, a_label_on_metal=False)
    with pytest.raises(CheckError, match="the label a at .* no Metal2"):
        check_top_harden.pin_lines(gds, "mac", PINNED)


def test_clean_gds_moves_a_cell_drawn_below_its_origin_to_it(tmp_path):
    """The R-2R DAC's vss strap below y=0 (breakage 35): the cleaned cell
    draws from (0, 0), its labels moved with its metal; a cell already
    there is left where it is."""
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("mac")
    cell.shapes(layout.layer(42, 0)).insert(db.DBox(-2.04, -3.2, 40, -1))
    cell.shapes(layout.layer(34, 0)).insert(db.DBox(0, 0, 10, 136.36))
    cell.shapes(layout.layer(34, 10)).insert(db.DText("a", 5, 5))
    src, dst = tmp_path / "src.gds", tmp_path / "dst.gds"
    layout.write(str(src))
    check_top_harden.clean_gds(src, dst, "mac")
    out = db.Layout()
    out.read(str(dst))
    top = out.cell("mac")
    assert top.dbbox() == db.DBox(0, 0, 42.04, 139.56)
    texts = [s.dtext for s in top.shapes(out.find_layer(34, 10)).each()]
    assert [(t.string, t.x, t.y) for t in texts] == [("a", 7.04, 8.2)]

    at_origin = tmp_path / "ok.gds"
    check_top_harden.clean_gds(dst, at_origin, "mac")
    again = db.Layout()
    again.read(str(at_origin))
    assert again.cell("mac").dbbox() == db.DBox(0, 0, 42.04, 139.56)
    with pytest.raises(CheckError, match="no cell 'nope'"):
        check_top_harden.clean_gds(src, dst, "nope")


def _dcf1a(region_of, box) -> "db.Region":
    """The PDK's DCF.1a (dummy_comp.rb) on one cell placed in a tile: 20 um
    or more with no COMP, COMP_Dummy or keep-out, counting LibreLane's 10
    um halo around the cell as bare and what lies past it as filled."""
    def ring(r, d):
        return r.sized(d * 1000) - r.sized(-d * 1000)
    keep = (region_of(110, 5) + ring(region_of(21, 0), 1)
            + ring(region_of(12, 0), 2))
    return (db.Region(box).sized(10000) - region_of(22, 0)
            - region_of(22, 4) - keep).sized(-10000).sized(10000)


def test_fill_macro_fills_the_comp_gap_and_keeps_off_the_circuit(tmp_path):
    """A macro whose devices sit at one end of a 120 um extent, as the R-2R
    DAC's left an unfilled COMP gap (breakage 37): fill_macro puts
    COMP_Dummy in the gap, so DCF.1a finds nothing, and keeps it off the
    COMP, poly, well edge and resistor marker by the PDK's own spaces and
    inside the macro's extent; it adds no other layer and moves no
    extent."""
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("mac")
    for spec, box in [((22, 0), (0, 0, 10, 4)), ((30, 0), (4, -1, 5, 5)),
                      ((21, 0), (-2, 10, 12, 20)), ((22, 0), (0, 12, 10, 16)),
                      ((110, 5), (60, 20, 70, 30)),
                      ((34, 0), (0, 0, 120, 1)), ((34, 0), (0, 59, 120, 60))]:
        cell.shapes(layout.layer(*spec)).insert(db.DBox(*box))
    cell.shapes(layout.layer(34, 10)).insert(db.DText("a", 1, 0.5))
    gds = tmp_path / "mac.gds"
    layout.write(str(gds))

    def regions(path):
        ly = db.Layout()
        ly.read(str(path))
        top = ly.cell("mac")

        def region_of(l, d):
            idx = ly.find_layer(l, d)
            return (db.Region() if idx is None
                    else db.Region(top.begin_shapes_rec(idx)).merged())
        return ly, top, region_of

    _ly, before, region_of = regions(gds)
    box = before.bbox()
    assert not _dcf1a(region_of, box).is_empty()  # the gap is there
    kept = {(i.layer, i.datatype): region_of(i.layer, i.datatype)
            for i in _ly.layer_infos()}

    squares = check_top_harden.fill_macro(gds, "mac")
    assert squares > 0
    ly, top, region_of = regions(gds)
    assert [c.name for c in ly.top_cells()] == ["mac"]
    assert top.bbox() == box
    assert _dcf1a(region_of, box).is_empty()
    fill = region_of(22, 4)
    assert fill.count() == squares
    assert {(i.layer, i.datatype) for i in ly.layer_infos()} == {
        *kept, (22, 4)}
    for spec, region in kept.items():
        assert (region_of(*spec) ^ region).is_empty(), spec
    assert fill.space_check(1900).is_empty()                      # DCF.2b
    assert fill.separation_check(region_of(22, 0), 3500).is_empty()  # DCF.4
    assert fill.separation_check(region_of(30, 0), 1500).is_empty()  # DCF.5
    assert fill.separation_check(region_of(21, 0), 1300).is_empty()  # 6a
    assert fill.separation_check(region_of(110, 5), 3500).is_empty()  # 8a
    assert (fill - db.Region(box)).is_empty()
    with pytest.raises(CheckError, match="no cell 'nope'"):
        check_top_harden.fill_macro(gds, "nope")


def test_lef_origin_reads_the_macro_origin(tmp_path):
    lef = tmp_path / "mac.lef"
    lef.write_text("MACRO mac\n  ORIGIN 2.040 3.200 ;\n"
                   "  SIZE 210.520 BY 139.560 ;\nEND mac\n")
    assert check_top_harden.lef_origin(lef) == (2.04, 3.2)
    lef.write_text("MACRO mac\n  SIZE 1.000 BY 1.000 ;\nEND mac\n")
    assert check_top_harden.lef_origin(lef) == (0.0, 0.0)


def test_lef_pins_too_small_names_point_and_missing_pins(tmp_path):
    lef = tmp_path / "mac.lef"
    lef.write_text("\n".join([
        "MACRO mac", "  PIN a", "    PORT", "      LAYER Metal2 ;",
        "        RECT 0.000 10.000 2.000 11.000 ;", "    END", "  END a",
        "  PIN b", "    PORT", "      LAYER Metal2 ;",
        "        RECT 0.495 10.495 0.505 10.505 ;", "    END", "  END b",
        "END mac", ""]))
    assert check_top_harden.lef_pins_too_small(lef, ["a", "b", "c"]) == [
        "b", "c"]


@pytest.mark.slow
def test_lef_offers_only_the_metal_under_each_port_label(tmp_path):
    """Against real magic: the abstract's pin for a is its Metal2 pad and
    the Metal1 strip its Via1 lands on is OBS (ece298a's R-2R DAC, where
    the router dropped a Via1 onto that strip); the unported net1 is OBS,
    not a pin; vss keeps its Metal3 rail for the PDN; and the abstract is
    still the drawn extent."""
    lef = check_top_harden.write_lef(_pinned_gds(tmp_path), "mac", [],
                                     PINNED)
    text = lef.read_text(encoding="utf-8")

    def block(start, end):
        m = re.search(rf"^\s*{start}\s*$(.*?)^\s*{end}\s*$", text,
                      re.MULTILINE | re.DOTALL)
        assert m, text
        return m.group(1)
    pin_a = block("PIN a", "END a")
    assert "LAYER Metal1" not in pin_a and "LAYER Via1" not in pin_a
    assert "RECT 0.000 10.000 2.000 11.000 ;" in pin_a
    obs = block("OBS", "END")
    assert "RECT 1.000 10.000 40.000 11.000 ;" in obs
    assert "RECT 1.370 10.370 1.630 10.630 ;" in obs
    assert "RECT 5.000 12.000 40.000 13.000 ;" in obs
    assert "PIN net1" not in text
    assert check_top_harden.lef_pin_layers(lef, "vss") == {"Metal1",
                                                           "Metal3"}
    assert check_top_harden.lef_size(lef) == (40.0, 20.0)
    assert check_top_harden.lef_pins_too_small(lef, list(PINNED)) == []


@pytest.mark.slow
def test_magic_lef_direction_survives_a_bus_bit_pin_name(tmp_path):
    """The regression itself, against real magic (not a fake): a synthetic
    cell with a width-2 bus pin and a scalar pin, run through the SAME
    port_lines()/tcl_tok() script builder analog_macro uses. Before the
    brace fix, magic ran dac_code[0]'s `[0]` as a nested Tcl command
    ("invalid command name" on stderr) and the bit's PIN block came out of
    `lef write` with no DIRECTION/USE at all - proved empirically against
    this same synthetic cell while writing this test."""
    import layoutlib

    cell = "buscell"
    pins = ["dac_code[0]", "dac_code[1]", "en"]
    layout = db.Layout()
    layout.dbu = 0.001
    top = layout.create_cell(cell)
    m1 = layout.layer(34, 0)          # GF180_LAYER["metal1"]
    m1_label = layout.layer(34, 10)   # GF180_LAYER["metal1_label"]
    for i, name in enumerate(pins):
        x0 = i * 2.0
        top.shapes(m1).insert(db.DBox(x0, 0, x0 + 1, 1))
        top.shapes(m1_label).insert(
            db.DText(name, x0 + 0.5, 0.5))
    gds = tmp_path / f"{cell}.gds"
    layout.write(str(gds))

    sig_pins = check_top_harden.signal_pins(
        {"dac_code": "d2a", "en": "a2d"}, {"dac_code": 2})
    lines = check_top_harden.port_lines(
        sig_pins, {"dac_code": "d2a", "en": "a2d"}, {})
    lef_out = tmp_path / f"{cell}.lef"
    script = "\n".join([
        "drc off", "crashbackups disable", "locking disable",
        f"gds read {gds}", f"load {cell}", "select top cell",
        "port makeall", *lines,
        "property LEFclass BLOCK", f"lef write {lef_out}",
        "quit -noprompt", ""])
    proc = layoutlib.run_eda(["magic", "-noconsole", "-dnull"], cwd=tmp_path,
                             timeout=120, stdin_text=script)
    layoutlib.require_ok(proc, "magic lef write")
    assert lef_out.is_file(), (proc.stdout or "")[-1500:]
    lef = lef_out.read_text(encoding="utf-8")
    for pin, want in (("dac_code[0]", "INPUT"), ("dac_code[1]", "INPUT"),
                      ("en", "OUTPUT")):
        m = re.search(
            r"PIN " + re.escape(pin) + r"\s*\n\s*DIRECTION (\w+)", lef)
        assert m, f"{pin}: no DIRECTION in LEF -\n{lef}"
        assert m.group(1) == want


# ------------------------------------------------ the analog tile, for real

# The r2r DAC's macro: 207.82 x 139.56 um drawn from (-2.04, -3.2), but its
# only PR boundary is the column of eight flattened buf_20 cells in one
# corner.
DAC_DRAWN = (-2.04, -3.2, 205.78, 136.36)
DAC_BOUNDARY = (165.52, 0.0, 202.48, 31.36)


@pytest.mark.slow
def test_lef_covers_a_macro_whose_boundary_is_one_flattened_cell(tmp_path):
    """The abstract is what the macro draws, not the corner a flattened
    standard cell's PR boundary marks (breakage 23, PDN-0179); an abstract
    magic leaves short of the drawing is refused."""
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("mac")
    x0, y0, x1, y1 = DAC_DRAWN
    for box in ((x0, y0, x0 + 1, y1), (x1 - 1, y0, x1, y1)):  # metal1
        cell.shapes(layout.layer(34, 0)).insert(db.DBox(*box))
    cell.shapes(layout.layer(0, 0)).insert(db.DBox(*DAC_BOUNDARY))
    gds = tmp_path / "mac.gds"
    layout.write(str(gds))

    # drawn below and left of (0, 0), magic writes ORIGIN 2.040 3.200,
    # which the final GDS would not honour (breakage 35): refused
    with pytest.raises(CheckError, match="ORIGIN 2.040 3.200"):
        check_top_harden.write_lef(gds, "mac", [], {})
    check_top_harden.clean_gds(gds, gds, "mac")
    lef = check_top_harden.write_lef(gds, "mac", [], {})
    assert check_top_harden.lef_size(lef) == (207.82, 139.56)
    assert check_top_harden.lef_origin(lef) == (0.0, 0.0)

    with pytest.raises(check_top_harden.CheckError, match="smaller than"):
        check_top_harden.write_lef(
            gds, "mac", ["property FIXED_BBOX {0 0 100 100}"], {})


@pytest.mark.slow
def test_dac_tile_goes_green_on_the_analog_tile_and_its_faults_go_red(
        tmp_path):
    """corpus/msde/dac_tile end to end on ONE real harden: vout on ua[0]
    hardens on the analog pin template and passes top_lvs and precheck; vout
    moved off the template is a top_harden finding with no harden; a ua pad
    info.yaml claims with nothing wired to it fails precheck. The planted
    faults are the corpus's own (faults/plant_*.py), which faults.py also
    runs, each on its own harden."""
    import yaml

    import check_precheck
    import check_top_lvs
    import faults

    rung = faults.CORPUS / "msde" / "dac_tile"
    off = faults.make_scratch_workspace(tmp_path / "off", rung, "msde",
                                        "dac_tile")
    faults.import_plant(rung, "plant_top_harden.py")(off)
    payload, _ = check_top_harden.run(["--workspace", str(off)])
    assert payload["status"] == "violations"
    assert {v["kind"] for v in payload["violations"]} == {
        "ua_pin_off_template"}
    assert not (off / "top" / "harden").exists()

    ws = faults.make_scratch_workspace(tmp_path / "ok", rung, "msde",
                                       "dac_tile")
    payload, _ = check_top_harden.run(["--workspace", str(ws)])
    assert payload["status"] == "pass", payload.get("violations")
    assert payload["macros"][0].get("ua") == {"vout": 0}
    for gate in (check_top_lvs, check_precheck):
        payload, _ = gate.run(["--workspace", str(ws)])
        assert payload["status"] == "pass", (gate.__name__,
                                             payload.get("violations"))

    spec_path = ws / "top" / "spec" / "spec.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    spec["macros"][0]["ua"]["unwired"] = 1
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False),
                         encoding="utf-8")
    payload, _ = check_precheck.run(["--workspace", str(ws)])
    assert payload["status"] == "violations"
    assert any(v["kind"] == "precheck_failed" and "ua[1]" in v["msg"]
               for v in payload["violations"]), payload["violations"]


@pytest.mark.slow
def test_a_macro_bounded_like_the_r2r_dac_goes_through_pdn(tmp_path):
    """corpus/msde/dac_tile with its PR boundary only in the lower-right
    quarter, as the r2r DAC's flattened buf_20 column left its own
    (breakage 23). With the abstract taken from that boundary, LibreLane
    put the drawn macro left of and below where top_harden centred it, and
    openroad-generatepdn failed. Now the abstract is the drawn extent, the
    macro sits where the unmodified rung's does, and top_drc, top_lvs and
    precheck pass on it."""
    import check_precheck
    import check_top_drc
    import check_top_lvs
    import faults

    rung = faults.CORPUS / "msde" / "dac_tile"
    ws = faults.make_scratch_workspace(tmp_path, rung, "msde", "dac_tile")
    gen = ws / "analog" / "layout" / "gen_dac_tile_analog.py"
    src = gen.read_text(encoding="utf-8")
    old = ("    layoutlib.rect(shifted, 0.0, 0.0, bb.width(), bb.height(), "
           "PR_BNDRY)\n")
    assert src.count(old) == 1
    gen.write_text(src.replace(old, (
        "    layoutlib.rect(shifted, bb.width() / 2, 0.0, bb.width(), "
        "bb.height() / 2, PR_BNDRY)\n")), encoding="utf-8")

    payload, _ = check_top_harden.run(["--workspace", str(ws)])
    assert payload["status"] == "pass", payload.get("violations")
    macros = ws / "top" / "macros"
    assert check_top_harden.lef_size(macros / "dac_tile_analog.lef") == \
        pytest.approx(check_top_harden.gds_extent(
            macros / "dac_tile_analog.gds", "dac_tile_analog"), abs=1.0)
    for gate in (check_top_drc, check_top_lvs, check_precheck):
        payload, _ = gate.run(["--workspace", str(ws)])
        assert payload["status"] == "pass", (gate.__name__,
                                             payload.get("violations"))


@pytest.mark.slow
def test_a_macro_drawn_off_its_origin_hardens_with_pins_on_their_routes(
        tmp_path):
    """corpus/msde/dac_tile with its whole layout moved 2.04 um left and
    3.2 um down, as the R-2R DAC's vss strap below y=0 left it (breakage
    35). Magic wrote LEF ORIGIN 2.040 3.200, OpenROAD routed to the
    abstract placed at the DEF point, and the final GDS put the cell's
    origin there, so every pin missed its route and top_lvs failed. Now
    the macro is moved to draw from (0, 0) first, and top_lvs passes."""
    import check_top_lvs
    import faults

    rung = faults.CORPUS / "msde" / "dac_tile"
    ws = faults.make_scratch_workspace(tmp_path, rung, "msde", "dac_tile")
    gen = ws / "analog" / "layout" / "gen_dac_tile_analog.py"
    src = gen.read_text(encoding="utf-8")
    old = ("    dx, dy = -bb.left, -bb.bottom\n"
           "    shifted = gf.Component()\n"
           "    shifted.add_ref(top).move((dx, dy))\n"
           "    layoutlib.rect(shifted, 0.0, 0.0, bb.width(), bb.height(), "
           "PR_BNDRY)\n")
    assert src.count(old) == 1
    gen.write_text(src.replace(old, (
        "    dx, dy = -bb.left - 2.04, -bb.bottom - 3.2\n"
        "    shifted = gf.Component()\n"
        "    shifted.add_ref(top).move((dx, dy))\n"
        "    layoutlib.rect(shifted, -2.04, -3.2, bb.width() - 2.04, "
        "bb.height() - 3.2, PR_BNDRY)\n")), encoding="utf-8")

    payload, _ = check_top_harden.run(["--workspace", str(ws)])
    assert payload["status"] == "pass", payload.get("violations")
    macros = ws / "top" / "macros"
    assert check_top_harden.lef_origin(macros / "dac_tile_analog.lef") == (
        0.0, 0.0)
    layout = db.Layout()
    layout.read(str(macros / "dac_tile_analog.gds"))
    box = layout.cell("dac_tile_analog").dbbox()
    assert (box.left, box.bottom) == (0.0, 0.0)
    payload, _ = check_top_lvs.run(["--workspace", str(ws)])
    assert payload["status"] == "pass", payload.get("violations")


@pytest.mark.slow
def test_a_macro_with_an_unfilled_comp_gap_passes_the_dummy_decks_at_top(
        tmp_path):
    """corpus/msde/dac_tile with its vdd and vss rails run 60 um further
    right and a vdd strap 40 um up from there, so the macro draws a COMP gap
    no fill reaches, as ece298a's R-2R DAC did (breakage 37: top_drc and the precheck each
    failed DCF.1a inside the macro, since the top's fill skips it and the
    block's DRC leaves the dummy decks off). Now top_harden fills the
    macro's gap with dummy COMP, and top_drc - dummy decks included -
    top_lvs and the precheck pass, with the LEF still the drawn extent at
    origin 0 0."""
    import check_precheck
    import check_top_drc
    import check_top_lvs
    import faults
    import layout_gen

    rung = faults.CORPUS / "msde" / "dac_tile"
    ws = faults.make_scratch_workspace(tmp_path, rung, "msde", "dac_tile")
    gen = ws / "analog" / "layout" / "gen_dac_tile_analog.py"
    src = gen.read_text(encoding="utf-8")
    old = ("        layoutlib.rect(top, 0.0, y0, x_right, y1, M1)\n"
           "        layoutlib.rect(top, 0.0, y0, x_right, y1, M3)\n")
    assert src.count(old) == 1
    gen.write_text(src.replace(old, (
        "        layoutlib.rect(top, 0.0, y0, x_right + 60.0, y1, M1)\n"
        "        layoutlib.rect(top, 0.0, y0, x_right + 60.0, y1, M3)\n"
        "        if net == \"vdd\":\n"
        "            layoutlib.rect(top, x_right + 60.0 - RAIL, y0, "
        "x_right + 60.0, y1 + 40.0, M3)\n")), encoding="utf-8")

    raw, cell, _abs, _abstract = layout_gen.build(ws / "analog")

    def gap(path: Path) -> "db.Region":
        ly = db.Layout()
        ly.read(str(path))
        top = ly.cell(cell)

        def region_of(l, d):
            idx = ly.find_layer(l, d)
            return (db.Region() if idx is None
                    else db.Region(top.begin_shapes_rec(idx)).merged())
        return _dcf1a(region_of, top.bbox())
    assert not gap(raw).is_empty()  # the gap the top's fill never reaches

    payload, _ = check_top_harden.run(["--workspace", str(ws)])
    assert payload["status"] == "pass", payload.get("violations")
    macros = ws / "top" / "macros"
    assert gap(macros / f"{cell}.gds").is_empty()
    assert check_top_harden.lef_origin(macros / f"{cell}.lef") == (0.0, 0.0)
    assert check_top_harden.lef_size(macros / f"{cell}.lef") == \
        pytest.approx(check_top_harden.gds_extent(
            macros / f"{cell}.gds", cell), abs=1.0)
    for gate in (check_top_drc, check_top_lvs, check_precheck):
        payload, _ = gate.run(["--workspace", str(ws)])
        assert payload["status"] == "pass", (gate.__name__,
                                             payload.get("violations"))
