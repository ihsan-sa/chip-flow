"""engine/lib/ttlib.py: the TT GF180 tile pin map (docs/design.md 1.5,
"### M4."). Pure logic, no toolchain needed - pin_budget()/tile_die_area()
read the vendored template files (engine/reference/tt/) directly, and
generate_tt_wrapper()/generate_glsim_harness() are text generation."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine"
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import ttlib  # noqa: E402

COUNTER8_SPEC = {
    "top": "counter8",
    "ports": {
        "clk": {"dir": "input", "width": 1},
        "rst": {"dir": "input", "width": 1},
        "count": {"dir": "output", "width": 8},
    },
    "tt_pins": {"clk": "clk", "rst": "~rst_n", "count": "uo_out[7:0]"},
}


def test_pin_budget_reads_the_vendored_def_template():
    budget = ttlib.pin_budget()
    assert budget == {"ui_in": 8, "uio_in": 8, "uo_out": 8, "uio_out": 8,
                      "uio_oe": 8}


def test_tile_die_area_reads_the_vendored_tile_sizes_yaml():
    assert ttlib.tile_die_area() == "0 0 346.64 160.72"
    with pytest.raises(ttlib.TTError, match="tiles='9x9'"):
        ttlib.tile_die_area("9x9")


def test_clean_spec_validates_with_no_problems():
    assert ttlib.validate_tt_pins(COUNTER8_SPEC) == []


def test_missing_tt_pins_mapping_is_a_problem():
    spec = {**COUNTER8_SPEC, "tt_pins": {}}
    problems = ttlib.validate_tt_pins(spec)
    assert any("no non-empty 'tt_pins'" in p for p in problems)


def test_port_with_no_mapping_is_a_problem():
    spec = {**COUNTER8_SPEC, "tt_pins": {"clk": "clk", "rst": "~rst_n"}}
    problems = ttlib.validate_tt_pins(spec)
    assert any("'count' has no tt_pins mapping" in p for p in problems)


def test_wrong_width_is_a_problem():
    spec = {**COUNTER8_SPEC,
            "tt_pins": {**COUNTER8_SPEC["tt_pins"], "count": "uo_out[3:0]"}}
    problems = ttlib.validate_tt_pins(spec)
    assert any("8 bit(s) wide but tt_pins maps it to 4 bit(s)" in p
              for p in problems)


def test_direction_mismatch_is_a_problem():
    spec = {**COUNTER8_SPEC,
            "tt_pins": {**COUNTER8_SPEC["tt_pins"], "clk": "uo_out[0]"}}
    problems = ttlib.validate_tt_pins(spec)
    assert any("'input' but maps to uo_out" in p for p in problems)


def test_inverted_output_is_a_problem():
    spec = {"top": "x", "ports": {"o": {"dir": "output", "width": 1}},
            "tt_pins": {"o": "~uo_out[0]"}}
    problems = ttlib.validate_tt_pins(spec)
    assert any("cannot be inverted" in p for p in problems)


def test_out_of_range_bit_is_a_problem():
    spec = {**COUNTER8_SPEC,
            "tt_pins": {**COUNTER8_SPEC["tt_pins"], "count": "uo_out[8:1]"}}
    problems = ttlib.validate_tt_pins(spec)
    assert any("only has uo_out[7:0]" in p for p in problems)


def test_overlapping_bits_is_a_problem():
    spec = {
        "top": "x",
        "ports": {"a": {"dir": "input", "width": 1},
                 "b": {"dir": "input", "width": 1}},
        "tt_pins": {"a": "ui_in[0]", "b": "ui_in[0]"},
    }
    problems = ttlib.validate_tt_pins(spec)
    assert any("already used by another port" in p for p in problems)


def test_tt_pins_naming_an_unknown_port_is_a_problem():
    spec = {**COUNTER8_SPEC,
            "tt_pins": {**COUNTER8_SPEC["tt_pins"], "ghost": "ui_in[0]"}}
    problems = ttlib.validate_tt_pins(spec)
    assert any("names ports not in spec.yaml" in p for p in problems)


def test_bad_expression_syntax_is_a_problem():
    spec = {**COUNTER8_SPEC,
            "tt_pins": {**COUNTER8_SPEC["tt_pins"], "count": "not_a_pin"}}
    problems = ttlib.validate_tt_pins(spec)
    assert any("does not match the grammar" in p for p in problems)


def test_generate_tt_wrapper_refuses_when_tt_pins_do_not_fit():
    spec = {**COUNTER8_SPEC, "tt_pins": {"clk": "clk", "rst": "~rst_n"}}
    with pytest.raises(ttlib.TTError, match="does not fit the tile"):
        ttlib.generate_tt_wrapper(spec)


def test_generate_tt_wrapper_wires_every_port_and_ties_off_the_rest():
    v = ttlib.generate_tt_wrapper(COUNTER8_SPEC)
    assert "module tt_um_counter8 (" in v
    assert "assign w_rst = ~rst_n;" in v
    assert "counter8 u_counter8" in v
    assert "uio_oe = 8'b00000000;" in v  # nothing maps to uio_out here


def test_generate_tt_wrapper_drives_uio_oe_for_used_uio_out_bits():
    spec = {
        "top": "x",
        "ports": {"a": {"dir": "output", "width": 1}},
        "tt_pins": {"a": "uio_out[3]"},
    }
    v = ttlib.generate_tt_wrapper(spec)
    assert "uio_oe = 8'b00001000;" in v


def test_generate_glsim_harness_is_the_reverse_wrapper():
    v = ttlib.generate_glsim_harness(COUNTER8_SPEC)
    assert "module counter8 (" in v
    assert "tt_um_counter8 dut (" in v
    assert "wire tt_rst_n = ~rst;" in v
    assert "$sdf_annotate" not in v


def test_generate_glsim_harness_adds_sdf_annotate_when_given_a_path():
    v = ttlib.generate_glsim_harness(COUNTER8_SPEC, sdf_path="/tmp/x.sdf")
    assert '$sdf_annotate("/tmp/x.sdf", dut);' in v


def test_wrapper_name_does_not_double_prefix():
    assert ttlib.wrapper_name({"top": "counter8"}) == "tt_um_counter8"
    assert ttlib.wrapper_name({"top": "tt_um_counter8"}) == "tt_um_counter8"


def test_gf180_tech_reads_the_vendored_tech_py():
    tech = ttlib.gf180_tech()
    assert tech.librelane_pdk_args == "--pdk gf180mcuD"
    assert tech.project_top_metal_layer == "Metal4"


def test_stdcell_liberty_path_resolves_from_vendored_lib_map(tmp_path):
    p = ttlib.stdcell_liberty_path("max_ss_125C_3v00", tmp_path)
    assert p == (tmp_path / "gf180mcuD" / "libs.ref" / "gf180mcu_fd_sc_mcu7t5v0" /
                "lib" / "gf180mcu_fd_sc_mcu7t5v0__ss_125C_3v00.lib")


def test_template_locked_keys_are_the_ones_below_the_do_not_change_marker():
    locked = ttlib.template_locked_keys()
    assert {"RUN_KLAYOUT_DRC", "FP_SIZING", "MAGIC_WRITE_LEF_PINONLY"} <= locked
    assert "PL_TARGET_DENSITY_PCT" not in locked  # a documented user knob


def test_harden_config_merges_override_last_and_refuses_owned_keys(tmp_path):
    spec = {**COUNTER8_SPEC, "clock": {"period_ns": 20}}
    rtl = [tmp_path / "counter8.v"]
    wrapper = tmp_path / "tt_um_counter8.v"
    config = ttlib.harden_config(spec, rtl, wrapper, tmp_path,
                                 override={"RUN_POST_GRT_RESIZER_TIMING": 1})
    assert config["RUN_POST_GRT_RESIZER_TIMING"] == 1
    forbidden = ttlib.forbidden_override_keys()
    assert {"CLOCK_PERIOD", "DIE_AREA", "VERILOG_FILES", "PDK_ROOT"} <= forbidden
    for key in ("CLOCK_PERIOD", "RUN_KLAYOUT_XOR", "LIB_SYNTH"):
        with pytest.raises(ttlib.TTError, match=key):
            ttlib.harden_config(spec, rtl, wrapper, tmp_path, override={key: 1})


def test_harden_override_may_not_replace_the_specs_macros(tmp_path):
    # spec.yaml `macros` owns macro_config()'s keys; an override merged last
    # would otherwise swap the hard macro out from under LVS.
    spec = {**COUNTER8_SPEC, "clock": {"period_ns": 20}}
    rtl = [tmp_path / "counter8.v"]
    wrapper = tmp_path / "tt_um_counter8.v"
    for key in ("MACROS", "PDN_MACRO_CONNECTIONS", "PDN_CFG",
                "MAGIC_EXT_USE_GDS", "EXTRA_SPICE_MODELS"):
        with pytest.raises(ttlib.TTError, match=key):
            ttlib.harden_config(spec, rtl, wrapper, tmp_path, override={key: 1})
    config = ttlib.harden_config(spec, rtl, wrapper, tmp_path,
                                 override={"PL_TARGET_DENSITY_PCT": 50})
    assert config["PL_TARGET_DENSITY_PCT"] == 50


def test_load_harden_override_absent_is_empty_and_bad_json_is_refused(tmp_path):
    assert ttlib.load_harden_override(tmp_path / "config.override.json") == {}
    bad = tmp_path / "config.override.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ttlib.TTError, match="not valid JSON"):
        ttlib.load_harden_override(bad)


# ---------------------------------------------------------------------------
# analog tile (tt-analog-tile): macro pins sent to the ua pads
# ---------------------------------------------------------------------------

def _analog_spec(ua):
    return {"top": "t", "ports": {"a": {"dir": "input", "width": 1},
                                  "b": {"dir": "output", "width": 1}},
            "tt_pins": {"a": "ui_in[0]"},
            "macros": [{"cell": "c", "instance": "u_analog",
                        "pins": {"b": "b"}, "ua": ua}]}


def test_analog_template_values_come_from_the_vendored_files():
    assert ttlib.analog_default_tiles() == "1x2"
    assert ttlib.analog_pin_limit() == 6
    assert ttlib.pin_budget("1x2", analog=True)["ua"] == 8
    assert "ua" not in ttlib.pin_budget()


def test_ua_pins_make_the_spec_an_analog_tile():
    spec = _analog_spec({"vout": 0})
    assert ttlib.is_analog(spec) and ttlib.spec_tiles(spec) == "1x2"
    assert ttlib.validate_tt_pins(spec) == []
    assert ttlib.def_template_path("1x2", analog=True).is_file()
    digital = _analog_spec({})
    assert not ttlib.is_analog(digital)
    assert ttlib.spec_tiles(digital) == ttlib.DEFAULT_TILES


def test_wrapper_wires_ua_pins_and_digital_wrapper_has_no_ua():
    text = ttlib.generate_tt_wrapper(_analog_spec({"vout": 0}))
    assert "inout  wire [7:0] ua," in text
    assert ".vout(ua[0])" in text and ".b(w_b)" in text
    assert "ua" not in ttlib.generate_tt_wrapper(_analog_spec({})).split(
        "u_analog")[0]


def test_ua_gap_duplicate_and_overflow_are_problems():
    gap = ttlib.validate_tt_pins(_analog_spec({"vout": 1}))
    assert any("no gap" in p for p in gap)
    spec = _analog_spec({"vout": 0})
    spec["macros"].append({"cell": "d", "instance": "u2", "pins": {},
                           "ua": {"x": 0}})
    assert any("claimed by both" in p for p in ttlib.validate_tt_pins(spec))
    many = _analog_spec({f"p{k}": k for k in range(7)})
    assert any("allows 6" in p for p in ttlib.validate_tt_pins(many))
    bound = _analog_spec({"b": 0})
    assert any("may not also go to a ua pad" in p
               for p in ttlib.validate_tt_pins(bound))


def test_no_analog_template_for_a_1x1_tile():
    spec = _analog_spec({"vout": 0})
    spec["tiles"] = "1x1"
    assert any("no vendored DEF template" in p
               for p in ttlib.validate_tt_pins(spec))


def test_harden_config_uses_the_analog_def(tmp_path):
    spec = _analog_spec({"vout": 0})
    spec["macros"][0].update(location=[10, 10], power={"vdd": "vdd",
                             "vss": "vss"}, files={k: "/x" for k in
                                                  ("gds", "lef", "vh", "spice")})
    cfg = ttlib.harden_config(spec, [], tmp_path / "w.v", tmp_path)
    assert cfg["FP_DEF_TEMPLATE"].endswith("analog/tt_analog_1x2.def")
    assert cfg["DIE_AREA"] == ttlib.tile_die_area("1x2")
    # the resizer leaves the pad nets alone: post-GRT repair_design stopped
    # on ua[0] with RSZ-0074, and a buffer there would sit in the analog path
    rx = re.compile(cfg["RSZ_DONT_TOUCH_RX"])
    assert all(rx.search(f"ua[{k}]") for k in range(8))
    assert not any(rx.search(n) for n in ("uo_out[0]", "ua", "u_ua[0]",
                                          "ua[0]_buf"))


def test_a_digital_tile_touches_every_net(tmp_path):
    spec = _analog_spec({})
    spec["macros"][0].update(location=[10, 10], power={"vdd": "vdd",
                             "vss": "vss"}, files={k: "/x" for k in
                                                  ("gds", "lef", "vh", "spice")})
    cfg = ttlib.harden_config(spec, [], tmp_path / "w.v", tmp_path)
    assert "RSZ_DONT_TOUCH_RX" not in cfg


def test_info_yaml_claims_exactly_the_used_ua_pins(tmp_path):
    import yaml
    ttlib.write_info_yaml(_analog_spec({"vout": 0, "vref": 1}),
                          tmp_path / "info.yaml")
    data = yaml.safe_load((tmp_path / "info.yaml").read_text())
    assert data["project"]["analog_pins"] == 2
    assert data["project"]["tiles"] == "1x2"
    assert data["pinout"] == {"ua[0]": "vout", "ua[1]": "vref"}
    ttlib.write_info_yaml(_analog_spec({}), tmp_path / "d.yaml")
    digital = yaml.safe_load((tmp_path / "d.yaml").read_text())
    assert "analog_pins" not in digital["project"]
    assert digital["project"]["tiles"] == ttlib.DEFAULT_TILES


def test_harden_config_repairs_to_the_limits_timing_signs_off(tmp_path):
    # Regression: a 37-flop block hardened with the PDK defaults failed the
    # timing gate on max cap at the CTS root (8 clkbuf_16 trunk inputs) and
    # on ss max slew after routing (post-GRT repair off). The generated
    # config must keep both repairs on; they come after the tech layer so a
    # template or tech default cannot turn them back off.
    config = ttlib.harden_config(COUNTER8_SPEC, [tmp_path / "counter8.v"],
                                 tmp_path / "tt_um_counter8.v", tmp_path)
    assert config["CTS_ROOT_BUFFER"] == "gf180mcu_fd_sc_mcu7t5v0__clkbuf_8"
    assert config["RUN_POST_GRT_DESIGN_REPAIR"] is True
    # the timing limits themselves stay the PDK's: nothing here loosens them
    for key in ("MAX_CAPACITANCE_CONSTRAINT", "MAX_TRANSITION_CONSTRAINT",
                "MAX_FANOUT_CONSTRAINT"):
        assert key not in config
