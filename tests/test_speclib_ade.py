"""engine/lib/speclib.py's lint_spec_ade: /ade's own spec.yaml shape (docs/
design.md 1.4, 1.5's ade spec_lint row, "### M8."). Hermetic (pure venv:
yaml)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "lib"))

import speclib  # noqa: E402

GOOD_SPEC = {
    "top": "current_mirror",
    "supply": {"vdd": 3.3},
    "devices": ["xmref", "xmout"],
    "corners": "default",
    "measures": [
        {"name": "iout_ratio", "bounds": {"min": 1.8, "max": 2.2}},
    ],
}


def test_clean_ade_spec_has_no_violations():
    assert speclib.lint_spec_ade(dict(GOOD_SPEC)) == []


def test_missing_top_is_a_violation():
    spec = dict(GOOD_SPEC)
    del spec["top"]
    kinds = {v["kind"] for v in speclib.lint_spec_ade(spec)}
    assert "no_top" in kinds


def test_missing_supply_is_a_violation():
    spec = {**GOOD_SPEC}
    del spec["supply"]
    kinds = {v["kind"] for v in speclib.lint_spec_ade(spec)}
    assert "no_supply" in kinds


def test_supply_vdd_must_be_a_positive_number():
    spec = {**GOOD_SPEC, "supply": {"vdd": -1}}
    kinds = {v["kind"] for v in speclib.lint_spec_ade(spec)}
    assert "no_supply" in kinds

    spec2 = {**GOOD_SPEC, "supply": {"vdd": "3.3"}}
    kinds2 = {v["kind"] for v in speclib.lint_spec_ade(spec2)}
    assert "no_supply" in kinds2


def test_missing_devices_is_a_violation():
    spec = {**GOOD_SPEC, "devices": []}
    kinds = {v["kind"] for v in speclib.lint_spec_ade(spec)}
    assert "no_devices" in kinds


def test_no_measures_is_a_violation():
    spec = {**GOOD_SPEC, "measures": []}
    kinds = {v["kind"] for v in speclib.lint_spec_ade(spec)}
    assert "no_measures" in kinds


def test_measure_without_bounds_is_the_named_fault():
    # gates.yaml's own fault for ade spec_lint: "a measure without bounds".
    spec = {**GOOD_SPEC, "measures": [{"name": "gain"}]}
    violations = speclib.lint_spec_ade(spec)
    kinds = {v["kind"] for v in violations}
    assert "measure_no_bounds" in kinds
    v = next(v for v in violations if v["kind"] == "measure_no_bounds")
    assert v["refs"] == ["gain"]


def test_measure_with_only_min_or_only_max_is_fine():
    spec = {**GOOD_SPEC, "measures": [{"name": "gain", "bounds": {"min": 1}}]}
    assert speclib.lint_spec_ade(spec) == []


def test_duplicate_measure_name_flagged():
    spec = {**GOOD_SPEC, "measures": [
        {"name": "gain", "bounds": {"min": 1}},
        {"name": "gain", "bounds": {"max": 2}},
    ]}
    kinds = {v["kind"] for v in speclib.lint_spec_ade(spec)}
    assert "measure_dup_name" in kinds


def test_measure_bad_corners_flagged():
    spec = {**GOOD_SPEC, "measures": [
        {"name": "gain", "bounds": {"min": 1}, "corners": "whenever"},
    ]}
    kinds = {v["kind"] for v in speclib.lint_spec_ade(spec)}
    assert "measure_bad_corners" in kinds

    spec2 = {**GOOD_SPEC, "measures": [
        {"name": "gain", "bounds": {"min": 1}, "corners": ["ss", "ff"]},
    ]}
    assert speclib.lint_spec_ade(spec2) == []


def test_measure_bad_severity_flagged():
    spec = {**GOOD_SPEC, "measures": [
        {"name": "gain", "bounds": {"min": 1}, "severity": "meh"},
    ]}
    kinds = {v["kind"] for v in speclib.lint_spec_ade(spec)}
    assert "measure_bad_severity" in kinds


def test_top_level_corners_field_validated():
    spec = {**GOOD_SPEC, "corners": "sometimes"}
    kinds = {v["kind"] for v in speclib.lint_spec_ade(spec)}
    assert "bad_corners" in kinds

    spec2 = {**GOOD_SPEC, "corners": ["tt", "ss"]}
    assert speclib.lint_spec_ade(spec2) == []


def test_mc_block_must_be_a_mapping_when_present():
    spec = {**GOOD_SPEC, "mc": "yes please"}
    kinds = {v["kind"] for v in speclib.lint_spec_ade(spec)}
    assert "mc_not_a_mapping" in kinds

    spec2 = {**GOOD_SPEC, "mc": {"enabled": True, "runs": 20, "yield_min": 0.95}}
    assert speclib.lint_spec_ade(spec2) == []


def test_ade_lint_does_not_require_requirements():
    # unlike vde's lint_spec, an ade spec with no 'requirements' at all is
    # not itself a fault - gates.yaml's ade spec_lint row never asks for it.
    spec = dict(GOOD_SPEC)
    assert "requirements" not in spec
    assert speclib.lint_spec_ade(spec) == []


# --------------------------------------------- lint_measures_vs_bench_bounds
# The bench-writer works in fresh context (docs/design.md 1.3) and writes
# tb/*.bounds.json independently of spec.yaml's own 'measures' list - these
# two declarations of "what gets checked" can drift apart with nothing else
# catching it.

def test_no_bench_bounds_yet_is_not_a_violation():
    # right after spec-writer runs, before any bench-writer step - nothing
    # to reconcile against yet is the ordinary state, not a fault.
    assert speclib.lint_measures_vs_bench_bounds(GOOD_SPEC, {}) == []


def test_matching_spec_and_bench_measures_is_clean():
    bench_bounds = {"mirror_tb.cir": [{"measure": "iout_ratio", "min": 1.8, "max": 2.2}]}
    assert speclib.lint_measures_vs_bench_bounds(GOOD_SPEC, bench_bounds) == []


def test_spec_measure_with_no_bench_bound_is_a_violation():
    # spec.yaml declares 'iout_ratio' but no tb/*.bounds.json sidecar
    # scores it - a requirement nothing actually checks.
    violations = speclib.lint_measures_vs_bench_bounds(GOOD_SPEC, {})
    assert violations == []  # {} means "no benches yet", not this case
    bench_bounds = {"mirror_tb.cir": [{"measure": "some_other_measure", "min": 1}]}
    violations = speclib.lint_measures_vs_bench_bounds(GOOD_SPEC, bench_bounds)
    kinds = {v["kind"] for v in violations}
    assert "measure_no_bench_bound" in kinds
    v = next(v for v in violations if v["kind"] == "measure_no_bench_bound")
    assert v["refs"] == ["iout_ratio"]


def test_bench_bound_with_no_spec_measure_is_a_violation():
    # the reverse: tb/*.bounds.json scores and enforces a measure spec.yaml
    # never declares - an undeclared requirement silently enforced.
    bench_bounds = {"mirror_tb.cir": [
        {"measure": "iout_ratio", "min": 1.8, "max": 2.2},
        {"measure": "extra_measure", "min": 0, "max": 1},
    ]}
    violations = speclib.lint_measures_vs_bench_bounds(GOOD_SPEC, bench_bounds)
    kinds = {v["kind"] for v in violations}
    assert "bench_bound_no_spec_measure" in kinds
    v = next(v for v in violations if v["kind"] == "bench_bound_no_spec_measure")
    assert v["refs"] == ["extra_measure"]


def test_both_directions_can_fire_at_once():
    bench_bounds = {"mirror_tb.cir": [{"measure": "extra_measure", "min": 0, "max": 1}]}
    kinds = {v["kind"] for v in
            speclib.lint_measures_vs_bench_bounds(GOOD_SPEC, bench_bounds)}
    assert kinds == {"measure_no_bench_bound", "bench_bound_no_spec_measure"}
