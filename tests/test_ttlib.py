"""engine/lib/ttlib.py: the TT GF180 tile pin map (docs/design.md 1.5,
"### M4."). Pure logic, no toolchain needed - pin_budget()/tile_die_area()
read the vendored template files (engine/reference/tt/) directly, and
generate_tt_wrapper()/generate_glsim_harness() are text generation."""
from __future__ import annotations

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


def test_load_harden_override_absent_is_empty_and_bad_json_is_refused(tmp_path):
    assert ttlib.load_harden_override(tmp_path / "config.override.json") == {}
    bad = tmp_path / "config.override.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ttlib.TTError, match="not valid JSON"):
        ttlib.load_harden_override(bad)
