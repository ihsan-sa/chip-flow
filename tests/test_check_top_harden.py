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
