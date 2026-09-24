"""engine/scripts/check_cosim.py: the cosim gate (docs/design.md 1.5's msde
cosim row, "### M10.").

CLAUDE.md's rule for this gate specifically: "ngspice exits 0 on many
failures, so parse the measures... Prove with tests that a non-converging
run, a bench where the digital side never toggled, or a missing measure
fails cosim." The three fast tests below exercise exactly those three
shapes against `evaluate_measures`/`scan_nonconvergence` directly - no
workspace, no subprocess, no real ngspice, so they are not `slow` and run
in `tests/check.sh`'s own budget. The slow tests at the bottom run the real
corpus/msde/ring_osc_div bench end to end (needs the eda image - cocotb,
Icarus, ngspice's shared library)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
CORPUS_RUNG = REPO / "corpus" / "msde" / "ring_osc_div"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import check_cosim  # noqa: E402

BOUNDS = {"divided_freq_hz": {"min": 10_000_000, "max": 20_000_000}}


# --------------------------------------------------------------------- fast
def test_missing_measures_file_fails():
    violations = check_cosim.evaluate_measures(None, BOUNDS)
    assert violations
    assert violations[0]["kind"] == "measures_missing"


def test_digital_side_never_toggled_fails():
    measures = {"divided_freq_hz": None, "digital_toggles": 0,
                "edge_times_ns": []}
    violations = check_cosim.evaluate_measures(measures, BOUNDS)
    kinds = {v["kind"] for v in violations}
    assert "digital_side_never_toggled" in kinds


def test_missing_measure_key_fails():
    measures = {"digital_toggles": 8, "edge_times_ns": list(range(8))}
    violations = check_cosim.evaluate_measures(measures, BOUNDS)
    kinds = {v["kind"] for v in violations}
    assert "measure_missing" in kinds


def test_measure_out_of_bounds_fails():
    measures = {"divided_freq_hz": 1.0, "digital_toggles": 8,
                "edge_times_ns": list(range(8))}
    violations = check_cosim.evaluate_measures(measures, BOUNDS)
    kinds = {v["kind"] for v in violations}
    assert "measure_out_of_bounds" in kinds


def test_clean_measures_pass():
    measures = {"divided_freq_hz": 15_151_515.0, "digital_toggles": 8,
                "edge_times_ns": list(range(8)),
                "spice_time_reached_ns": 700.0}
    assert check_cosim.evaluate_measures(measures, BOUNDS) == []


def test_spice_time_missing_fails():
    # digital_toggles/edge_times_ns claim a toggled digital side, but
    # nothing records how far the analog bridge itself got - exactly what a
    # Python-only toggle (a counter with no real analog block behind it)
    # would leave behind.
    measures = {"divided_freq_hz": 15_151_515.0, "digital_toggles": 8,
                "edge_times_ns": list(range(8))}
    violations = check_cosim.evaluate_measures(measures, BOUNDS)
    kinds = {v["kind"] for v in violations}
    assert "spice_time_missing" in kinds


def test_spice_time_behind_last_edge_fails():
    # the analog bridge only reached t=3ns, but the digital side claims an
    # edge at t=7ns - the analog side never actually got there.
    measures = {"divided_freq_hz": 15_151_515.0, "digital_toggles": 8,
                "edge_times_ns": list(range(8)), "spice_time_reached_ns": 3.0}
    violations = check_cosim.evaluate_measures(measures, BOUNDS)
    kinds = {v["kind"] for v in violations}
    assert "spice_time_behind_last_edge" in kinds


def test_digital_toggles_unbacked_by_edges_fails():
    # digital_toggles is a bare literal - "8" with no matching edge times
    # recorded behind it.
    measures = {"divided_freq_hz": 15_151_515.0, "digital_toggles": 8,
                "edge_times_ns": [], "spice_time_reached_ns": 700.0}
    violations = check_cosim.evaluate_measures(measures, BOUNDS)
    kinds = {v["kind"] for v in violations}
    assert "digital_toggles_unbacked" in kinds


def test_scan_nonconvergence_catches_timestep_collapse():
    log = ('doAnalyses: TRAN:  Timestep too small; time = 5e-08, '
          'timestep = 0: trouble with node "x1.mid"')
    hits = check_cosim.scan_nonconvergence(log)
    assert hits, "a real ngspice non-convergence signature must be caught"


def test_scan_nonconvergence_clean_log_finds_nothing():
    log = "TESTS=1 PASS=1 FAIL=0\n"
    assert check_cosim.scan_nonconvergence(log) == []


def test_non_convergence_signature_fails_even_with_good_measures():
    # the brief's exact case: a run that logged a real non-convergence
    # signature must fail even though the measures it happened to record
    # look clean - "ngspice exits 0 on many failures" means the measures
    # alone are not the whole story either.
    log = 'doAnalyses: TRAN:  Timestep too small; trouble with node "x1.mid"'
    measures = {"divided_freq_hz": 15_151_515.0, "digital_toggles": 8,
                "edge_times_ns": list(range(8)),
                "spice_time_reached_ns": 700.0}
    assert check_cosim.scan_nonconvergence(log)
    assert check_cosim.evaluate_measures(measures, BOUNDS) == []
    # check_cosim.run() is what combines the two - see the slow tests below
    # for that combination exercised end to end.


def test_scan_ic_ignored_catches_non_existent_node():
    # docs/spikes/dcosim.md's own bug: an .ic naming a node without the
    # wrapper netlist's "x1." instance prefix - ngspice does not error, it
    # logs this and silently drops the initial condition.
    log = "Warning: IC on non-existent node - mid, ignored\n"
    hits = check_cosim.scan_ic_ignored(log)
    assert hits == ["mid"]


def test_scan_ic_ignored_clean_log_finds_nothing():
    log = "TESTS=1 PASS=1 FAIL=0\n"
    assert check_cosim.scan_ic_ignored(log) == []


def test_run_detects_ngspice_non_convergence_via_monkeypatched_run_cocotb(
        tmp_path, monkeypatch, capsys):
    # Nothing drove run()'s own combination of a monkeypatched cocotb result
    # with the raw sim log scan before this - the three fast tests above
    # exercise evaluate_measures/scan_nonconvergence directly, never run()
    # itself, so a bug in how run() wires the two together (e.g. reading the
    # wrong sim.log path, or never scanning it at all) had no test to catch
    # it. cocotblib.run_cocotb is monkeypatched to skip cocotb/Icarus/ngspice
    # entirely: it writes a sim.log carrying a real non-convergence
    # signature alongside a results.xml AND a cosim_measures.json that both
    # look clean, so the only way this test can fail is if run() itself
    # never reads the sim log.
    ws = tmp_path / "ws"
    tb = ws / "tb"
    tb.mkdir(parents=True)
    (ws / "interface.yaml").write_text("signals: []\n", encoding="utf-8")
    (tb / "top.v").write_text("module top; endmodule\n", encoding="utf-8")
    (tb / "test_top.py").write_text("", encoding="utf-8")
    (tb / "ideal.sp").write_text("* stub netlist, never simulated\n",
                                 encoding="utf-8")
    (tb / "bounds.json").write_text(
        json.dumps({"m": {"min": 0, "max": 1}}), encoding="utf-8")
    (tb / "cosim_bench.json").write_text(json.dumps({
        "top": "top", "bounds": "bounds.json",
        "analog_netlist": "ideal.sp", "analog_kind": "ideal",
    }), encoding="utf-8")

    def fake_run_cocotb(build_dir, test_dir, sources, hdl_toplevel,
                        test_modules_, results_xml, **kwargs):
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "sim.log").write_text(
            'doAnalyses: TRAN:  Timestep too small; time = 5e-08, '
            'timestep = 0: trouble with node "x1.mid"\n', encoding="utf-8")
        results_xml.parent.mkdir(parents=True, exist_ok=True)
        results_xml.write_text(
            '<testsuites><testsuite>'
            '<testcase name="test_top.test_top_case"/>'
            '</testsuite></testsuites>', encoding="utf-8")
        reports_dir = ws / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "cosim_measures.json").write_text(json.dumps({
            "m": 0.5, "digital_toggles": 1, "edge_times_ns": [1.0],
            "spice_time_reached_ns": 2.0,
        }), encoding="utf-8")
        return results_xml

    monkeypatch.setattr(check_cosim.cocotblib, "run_cocotb", fake_run_cocotb)

    code = check_cosim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "ngspice_non_convergence" in kinds


# --------------------------------------------------------------------- slow
def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "log").mkdir(parents=True)
    (ws / "reports").mkdir(parents=True)
    shutil.copytree(CORPUS_RUNG / "tb", ws / "tb")
    shutil.copy2(CORPUS_RUNG / "interface.yaml", ws / "interface.yaml")
    return ws


@pytest.mark.slow
def test_ring_osc_div_clean_bench_passes(tmp_path, capsys):
    ws = make_ws(tmp_path)
    code = check_cosim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["measures"]["digital_toggles"] >= 8
    assert not out["ngspice_signatures"]


@pytest.mark.slow
def test_ring_osc_div_never_toggled_fault_caught(tmp_path, capsys):
    ws = make_ws(tmp_path)
    sys.path.insert(0, str(CORPUS_RUNG / "faults"))
    import plant_never_toggled
    plant_never_toggled.plant(ws)
    code = check_cosim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "digital_side_never_toggled" in kinds


@pytest.mark.slow
def test_ring_osc_div_wrong_divide_ratio_fault_caught(tmp_path, capsys):
    ws = make_ws(tmp_path)
    sys.path.insert(0, str(CORPUS_RUNG / "faults"))
    import plant_wrong_divide_ratio
    plant_wrong_divide_ratio.plant(ws)
    code = check_cosim.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "measure_out_of_bounds" in kinds
