"""engine/lib/simlib.py: bounds sidecars, measure parsing, deck templating
and ngspice failure detection (docs/design.md 1.3, 1.5, "### M8."). Hermetic
- every text sample here is a REAL `eda ngspice -b` transcript captured
against this box's own gf180mcuD models while building this gate (see the
module docstring for how each was produced), not invented text."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import simlib  # noqa: E402
from checklib import CheckError  # noqa: E402

# A real `.op` transcript: v(d), v(floatgate) printed with no error at all.
CLEAN_STDOUT = """\

Note: No compatibility mode selected!


Circuit: * clean nfet op

No. of Data Rows : 1
v(d) = 3.300000e+00
v(g) = 1.800000e+00
"""

# A real transient `.measure` transcript (two-inverter chain, op-start).
TRAN_MEASURE_STDOUT = """\
Note: Starting dynamic gmin stepping
      * two inverter chain
      Transient Analysis  Thu Sep 24 14:26:15  2026
--------------------------------------------------------------------------------
Index   time            v(out)
--------------------------------------------------------------------------------
0	1.000000e-12	9.567195e-01

  Measurements for Transient Analysis

vout_final           =  3.30000e+00
trise                =  5.20000e-09

Total analysis time (seconds) = 0.00183548
"""

# A real stderr transcript: a floating gate node. singular matrix -> gmin
# stepping -> source stepping, all WARNINGS, ngspice still "succeeds" and
# exits 0 (confirmed live against this box's ngspice, see simlib.py's
# module docstring).
FLOATING_NODE_STDERR = """\
Warning: can't find the initialization file spinit.
Warning: singular matrix:  check node floatgate

Note: Starting dynamic gmin stepping
Warning: singular matrix:  check node floatgate

Warning: Dynamic gmin stepping failed
Note: Starting true gmin stepping
Warning: singular matrix:  check node floatgate

Warning: True gmin stepping failed
Note: Starting source stepping
Warning: source stepping failed
Note: Transient op started
Note: Transient op finished successfully
"""

# A real stderr transcript: an unresolvable device model (exit 1 in
# practice, but this text alone must be enough - never trust the exit code).
UNKNOWN_MODEL_STDERR = """\
Warning: can't find the initialization file spinit.
Error: unknown subckt: xm1 d g 0 0 nfet_99v9 w=10e-6 l=0.5e-6
    in line no. 6 from file bad_model.sp
    Simulation interrupted due to error!

Error: incomplete or empty netlist
       or no ".plot", ".print", or ".fourier" lines in batch mode;
no simulations run!
"""

# A real stderr transcript: a `.measure ... when ...` whose trigger never
# fires.
MEASURE_FAILED_STDERR = """\
Warning: can't find the initialization file spinit.

Error: measure  never_happens  find(AT) : out of interval
 .measure tran never_happens find v(d) when v(d)=99 failed!


Error: measure  never_happens  find(AT) : out of interval
 .measure tran never_happens find v(d) when v(d)=99 failed!
"""


# --------------------------------------------------------------- parse_measures

def test_parse_measures_reads_the_results_block():
    got = simlib.parse_measures(TRAN_MEASURE_STDOUT)
    assert got == {"vout_final": pytest.approx(3.3), "trise": pytest.approx(5.2e-9)}


def test_parse_measures_ignores_the_waveform_table():
    # the Index/time/v(out) table above the measures block must not be
    # picked up as measures.
    got = simlib.parse_measures(TRAN_MEASURE_STDOUT)
    assert "index" not in got and "time" not in got


def test_parse_measures_empty_when_no_header():
    assert simlib.parse_measures(CLEAN_STDOUT) == {}


def test_parse_failed_measures_real_transcript():
    assert simlib.parse_failed_measures(MEASURE_FAILED_STDERR) == {"never_happens"}


def test_parse_failed_measures_empty_on_clean_run():
    assert simlib.parse_failed_measures(FLOATING_NODE_STDERR) == set()


# ----------------------------------------------------------- engine errors

def test_floating_node_detected_despite_exit_0_and_clean_prints():
    # THE rule this milestone exists to prove: a run that "succeeds" (every
    # print/measure came back, exit would be 0) but only after singular-
    # matrix/gmin/source-stepping recovery must still be caught.
    kinds = simlib.detect_engine_errors(FLOATING_NODE_STDERR)
    assert "singular_matrix" in kinds
    assert "gmin_stepping_failed" in kinds
    assert "source_stepping_failed" in kinds


def test_unknown_model_detected():
    kinds = simlib.detect_engine_errors(UNKNOWN_MODEL_STDERR)
    assert "unknown_subckt" in kinds
    assert "empty_netlist" in kinds


def test_clean_run_has_no_engine_errors():
    assert simlib.detect_engine_errors(CLEAN_STDOUT) == []
    assert simlib.detect_engine_errors(TRAN_MEASURE_STDOUT) == []


def test_engine_error_violations_are_error_severity_and_name_the_corner():
    vs = simlib.engine_error_violations("sim_tt", "tb/mirror.cir", "ss",
                                        ["singular_matrix"], "check node x")
    assert len(vs) == 1
    assert vs[0]["severity"] == "error"
    assert vs[0]["refs"] == ["ss"]
    assert "singular matrix" in vs[0]["msg"]


# --------------------------------------------------------------- load_bounds

def test_load_bounds_valid_sidecar(tmp_path):
    p = tmp_path / "b.bounds.json"
    p.write_text(json.dumps([
        {"measure": "iout_ratio", "min": 1.8, "max": 2.2},
        {"measure": "trise", "max": 5e-9, "severity": "warning"},
    ]), encoding="utf-8")
    bounds = simlib.load_bounds(p)
    assert bounds[0]["severity"] == "error"  # default
    assert bounds[1]["severity"] == "warning"


def test_load_bounds_not_a_list_raises(tmp_path):
    p = tmp_path / "b.bounds.json"
    p.write_text(json.dumps({"measure": "x", "min": 1}), encoding="utf-8")
    with pytest.raises(CheckError):
        simlib.load_bounds(p)


def test_load_bounds_empty_list_raises(tmp_path):
    p = tmp_path / "b.bounds.json"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(CheckError):
        simlib.load_bounds(p)


def test_load_bounds_no_min_or_max_raises(tmp_path):
    p = tmp_path / "b.bounds.json"
    p.write_text(json.dumps([{"measure": "x"}]), encoding="utf-8")
    with pytest.raises(CheckError):
        simlib.load_bounds(p)


def test_load_bounds_bad_severity_raises(tmp_path):
    p = tmp_path / "b.bounds.json"
    p.write_text(json.dumps([{"measure": "x", "min": 1, "severity": "meh"}]),
                encoding="utf-8")
    with pytest.raises(CheckError):
        simlib.load_bounds(p)


def test_load_bounds_nan_rejected(tmp_path):
    p = tmp_path / "b.bounds.json"
    p.write_text('[{"measure": "x", "min": NaN}]', encoding="utf-8")
    with pytest.raises(CheckError):
        simlib.load_bounds(p)


def test_load_bounds_corners_field(tmp_path):
    p = tmp_path / "b.bounds.json"
    p.write_text(json.dumps([{"measure": "x", "min": 1, "corners": ["tt"]},
                             {"measure": "y", "min": 1, "corners": "all"}]),
                 encoding="utf-8")
    assert [b["corners"] for b in simlib.load_bounds(p)] == [["tt"], "all"]
    for bad in ([], "tt", [1], "default"):
        p.write_text(json.dumps([{"measure": "x", "min": 1, "corners": bad}]),
                     encoding="utf-8")
        with pytest.raises(CheckError):
            simlib.load_bounds(p)


# ------------------------------------------------------------ compare_bounds

BOUNDS = [
    {"measure": "iout_ratio", "min": 1.8, "max": 2.2, "severity": "error"},
    {"measure": "vout", "min": 1.0, "max": 1.5, "severity": "error"},
]


def test_compare_bounds_passes_clean_measures():
    out = simlib.compare_bounds(BOUNDS, {"iout_ratio": 2.0, "vout": 1.2},
                                "tb/mirror.cir")
    assert out == []


def test_compare_bounds_out_of_range_is_sim_bound_fail():
    out = simlib.compare_bounds(BOUNDS, {"iout_ratio": 3.5, "vout": 1.2},
                                "tb/mirror.cir", corner="ss")
    assert len(out) == 1
    assert out[0]["kind"] == "sim_bound_fail"
    assert out[0]["refs"] == ["iout_ratio", "ss"]


def test_compare_bounds_never_printed_is_sim_measure_missing():
    # "a measure it never printed fails the gate, not passes" - the rule
    # this milestone's brief names explicitly.
    out = simlib.compare_bounds(BOUNDS, {"iout_ratio": 2.0}, "tb/mirror.cir")
    assert len(out) == 1
    assert out[0]["kind"] == "sim_measure_missing"
    assert out[0]["refs"] == sorted(["vout", "tt"])


def test_compare_bounds_failed_trigger_is_also_sim_measure_missing():
    out = simlib.compare_bounds(BOUNDS, {"iout_ratio": 2.0, "vout": 1.2},
                                "tb/mirror.cir", failed_measures={"vout"})
    assert len(out) == 1
    assert out[0]["kind"] == "sim_measure_missing"
    assert "never met" in out[0]["msg"]


def test_compare_bounds_nonfinite_value_fails():
    out = simlib.compare_bounds(BOUNDS, {"iout_ratio": float("inf"), "vout": 1.2},
                                "tb/mirror.cir")
    assert len(out) == 1 and out[0]["kind"] == "sim_bound_fail"


def test_compare_bounds_warning_severity_carried_through():
    b = [{"measure": "x", "min": 1, "max": 2, "severity": "warning"}]
    out = simlib.compare_bounds(b, {"x": 5}, "tb/x.cir")
    assert out[0]["severity"] == "warning"


def test_compare_bounds_tt_only_bound_skipped_at_other_corners():
    # a spec measure scored at [tt] only (an oscillator's typical-band
    # frequency): out of range at ss is not a finding, nor is never
    # printing it there; the same value at tt is.
    b = [{"measure": "fosc", "min": 9e6, "max": 11e6, "severity": "error",
          "corners": ["tt"]}]
    assert simlib.compare_bounds(b, {"fosc": 6e6}, "tb/osc.cir",
                                 corner="ss") == []
    assert simlib.compare_bounds(b, {}, "tb/osc.cir", corner="ss") == []
    out = simlib.compare_bounds(b, {"fosc": 6e6}, "tb/osc.cir", corner="tt")
    assert [v["kind"] for v in out] == ["sim_bound_fail"]
    assert out[0]["refs"] == ["fosc", "tt"]


def test_compare_bounds_corners_all_scores_every_corner():
    b = [{"measure": "x", "min": 1, "max": 2, "severity": "error",
          "corners": "all"}]
    out = simlib.compare_bounds(b, {"x": 5}, "tb/x.cir", corner="ff")
    assert out[0]["refs"] == sorted(["x", "ff"])


# ---------------------------------------------------------------- materialize

def test_materialize_substitutes_all_placeholders():
    tpl = ".lib '{{PDK}}/x.spice' {{CORNER}}\n.temp {{TEMP_C}}\n"
    out = simlib.materialize(tpl, {"PDK": "/pdk", "CORNER": "ss", "TEMP_C": 125})
    assert out == ".lib '/pdk/x.spice' ss\n.temp 125\n"


def test_materialize_missing_substitution_raises():
    with pytest.raises(CheckError):
        simlib.materialize("{{NOPE}}", {})


def test_materialize_never_confused_by_single_brace_spice_expr():
    # ngspice's OWN {expr} syntax must survive untouched.
    tpl = "m1 d g s b nfet_03v3 w={wn} l=1e-6\n{{SIZING}}\n"
    out = simlib.materialize(tpl, {"SIZING": ".param wn=2e-6"})
    assert "w={wn}" in out
    assert ".param wn=2e-6" in out


def test_sizing_param_line_empty_is_a_comment():
    assert simlib.sizing_param_line({}).startswith("*")


def test_sizing_param_line_renders_values():
    line = simlib.sizing_param_line({"r2_length": {"value": 2e-5, "min": 1e-6,
                                                    "max": 5e-5}})
    assert "r2_length=2e-05" in line


# ------------------------------------------------------------------- run_ngspice

def test_run_ngspice_returns_stdout_stderr_returncode(monkeypatch, tmp_path):
    calls = {}

    class FakeProc:
        stdout = "ok\n"
        stderr = ""
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return FakeProc()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    out, err, rc = simlib.run_ngspice(Path("/bin/eda"), tmp_path / "d.cir",
                                      tmp_path, 30.0)
    assert (out, err, rc) == ("ok\n", "", 0)
    assert calls["cmd"][:3] == ["/bin/eda", "ngspice", "-b"]


def test_run_ngspice_timeout_is_reported_not_raised(monkeypatch, tmp_path):
    import subprocess

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", fake_run)
    out, err, rc = simlib.run_ngspice(Path("/bin/eda"), tmp_path / "d.cir",
                                      tmp_path, 5.0)
    assert rc == -1
    assert "timed out" in err


def test_run_ngspice_relative_deck_with_cwd_resolves(tmp_path, monkeypatch):
    """A relative deck path (from a relative --workspace) run with
    cwd=its own dir must still name the deck ngspice can open - it used to
    be resolved against cwd a second time (log/sim/log/sim/...), so every
    measure read as sim_measure_missing."""
    monkeypatch.chdir(tmp_path)
    out_dir = Path("log") / "sim"
    out_dir.mkdir(parents=True)
    deck = out_dir / "d.cir"
    deck.write_text("* deck\n", encoding="utf-8")
    fake = tmp_path / "fake_eda"
    fake.write_text('#!/bin/sh\ntest -f "$3" && echo found\n',
                    encoding="utf-8")
    fake.chmod(0o755)
    out, _err, rc = simlib.run_ngspice(fake, deck, out_dir, timeout=10)
    assert rc == 0 and "found" in out
