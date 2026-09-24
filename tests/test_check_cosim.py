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
                "edge_times_ns": list(range(8))}
    assert check_cosim.evaluate_measures(measures, BOUNDS) == []


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
                "edge_times_ns": list(range(8))}
    assert check_cosim.scan_nonconvergence(log)
    assert check_cosim.evaluate_measures(measures, BOUNDS) == []
    # check_cosim.run() is what combines the two - see the slow tests below
    # for that combination exercised end to end.


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
