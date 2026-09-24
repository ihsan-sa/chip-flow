"""engine/lib/speclib.py: spec.yaml parsing and the spec_lint rules (docs/
design.md 1.4, 1.5's spec_lint row). Hermetic (pure venv: yaml)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import speclib  # noqa: E402
from checklib import CheckError  # noqa: E402

GOOD_SPEC = {
    "top": "counter8",
    "requirements": [
        {"id": "REQ-WRAP", "text": "count wraps 15 -> 0", "check": "sim"},
        {"id": "REQ-RESET", "text": "rst clears count", "check": "sim"},
    ],
    "clock": {"period_ns": 10, "domains": ["clk"]},
}


def test_clean_spec_has_no_violations():
    assert speclib.lint_spec(dict(GOOD_SPEC)) == []


def test_missing_top_is_a_violation():
    spec = dict(GOOD_SPEC)
    del spec["top"]
    kinds = {v["kind"] for v in speclib.lint_spec(spec)}
    assert "no_top" in kinds


def test_empty_top_is_a_violation():
    spec = {**GOOD_SPEC, "top": "   "}
    kinds = {v["kind"] for v in speclib.lint_spec(spec)}
    assert "no_top" in kinds


def test_no_requirements_is_a_violation():
    spec = {**GOOD_SPEC, "requirements": []}
    kinds = {v["kind"] for v in speclib.lint_spec(spec)}
    assert "no_requirements" in kinds


def test_requirement_with_no_check_kind_fails():
    # the exact fault gates.yaml names for spec_lint: "a requirement with
    # no way to check it".
    spec = {**GOOD_SPEC, "requirements": [
        {"id": "REQ-X", "text": "something"},
    ]}
    violations = speclib.lint_spec(spec)
    kinds = {v["kind"] for v in violations}
    assert "requirement_no_check" in kinds
    v = next(v for v in violations if v["kind"] == "requirement_no_check")
    assert v["refs"] == ["REQ-X"]
    assert v["severity"] == "error"


def test_requirement_with_bad_check_kind_fails():
    spec = {**GOOD_SPEC, "requirements": [
        {"id": "REQ-X", "text": "something", "check": "vibes"},
    ]}
    kinds = {v["kind"] for v in speclib.lint_spec(spec)}
    assert "requirement_no_check" in kinds


def test_requirement_no_id_or_text_flagged():
    spec = {**GOOD_SPEC, "requirements": [
        {"check": "sim"},
        {"id": "REQ-Y", "check": "sim"},
    ]}
    kinds = {v["kind"] for v in speclib.lint_spec(spec)}
    assert "requirement_no_id" in kinds
    assert "requirement_no_text" in kinds


def test_duplicate_requirement_id_flagged():
    spec = {**GOOD_SPEC, "requirements": [
        {"id": "REQ-A", "text": "t1", "check": "sim"},
        {"id": "REQ-A", "text": "t2", "check": "sim"},
    ]}
    kinds = {v["kind"] for v in speclib.lint_spec(spec)}
    assert "requirement_dup_id" in kinds


def test_measure_requirement_needs_bounds():
    spec = {**GOOD_SPEC, "requirements": [
        {"id": "REQ-M", "text": "gain in range", "check": "measure"},
    ]}
    kinds = {v["kind"] for v in speclib.lint_spec(spec)}
    assert "requirement_measure_no_bounds" in kinds

    spec2 = {**GOOD_SPEC, "requirements": [
        {"id": "REQ-M", "text": "gain in range", "check": "measure",
         "bounds": {"min": 1, "max": 2}},
    ]}
    assert speclib.lint_spec(spec2) == []


def test_tt_pins_must_be_a_non_empty_mapping_when_present():
    spec = {**GOOD_SPEC, "tt_pins": {}}
    kinds = {v["kind"] for v in speclib.lint_spec(spec)}
    assert "tt_pins_empty" in kinds

    spec2 = {**GOOD_SPEC, "tt_pins": {"ui_in[0]": "clk"}}
    assert speclib.lint_spec(spec2) == []


def test_bad_clock_flagged():
    spec = {**GOOD_SPEC, "clock": {"period_ns": -1, "domains": []}}
    kinds = {v["kind"] for v in speclib.lint_spec(spec)}
    assert "clock_bad_period" in kinds
    assert "clock_no_domains" in kinds


def test_load_spec_missing_file_raises_check_error(tmp_path):
    with pytest.raises(CheckError):
        speclib.load_spec(tmp_path / "nope.yaml")


def test_load_spec_non_mapping_raises_check_error(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(CheckError):
        speclib.load_spec(p)


def test_load_spec_reads_real_yaml(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text("top: foo\nrequirements: []\n", encoding="utf-8")
    data = speclib.load_spec(p)
    assert data == {"top": "foo", "requirements": []}
