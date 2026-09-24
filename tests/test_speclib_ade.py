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
