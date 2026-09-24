"""engine/scripts/check_split.py: the split gate (docs/design.md 1.5's msde
split row, "### M10."). Pure YAML parsing/comparison - no toolchain, no
eda image, nothing here is `slow`."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_split  # noqa: E402
from checklib import CheckError  # noqa: E402

INTERFACE = """\
version: 1
signals:
  - name: osc_out
    direction: a2d
    level: cmos_3v3
    domain: clk_free
    width: 1
    load: 2pF
  - name: osc_en
    direction: d2a
    level: cmos_3v3
    domain: clk_sys
    width: 1
    load: 4pF
"""

DIGITAL_SPEC = """\
top: sensor_counted_digital
interface:
  - name: osc_out
    direction: a2d
    level: cmos_3v3
    domain: clk_free
    width: 1
    load: 2pF
  - name: osc_en
    direction: d2a
    level: cmos_3v3
    domain: clk_sys
    width: 1
    load: 4pF
"""

ANALOG_SPEC = DIGITAL_SPEC.replace("sensor_counted_digital", "sensor_counted_analog")


def make_ws(tmp_path: Path, digital=DIGITAL_SPEC, analog=ANALOG_SPEC,
           interface=INTERFACE) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "interface.yaml").write_text(interface, encoding="utf-8")
    (ws / "digital_spec.yaml").write_text(digital, encoding="utf-8")
    (ws / "analog_spec.yaml").write_text(analog, encoding="utf-8")
    return ws


def test_clean_passes(tmp_path):
    ws = make_ws(tmp_path)
    payload, _out = check_split.run(["--workspace", str(ws)])
    assert payload["status"] == "pass"
    assert payload["violations"] == []
    assert payload["signals"] == ["osc_en", "osc_out"]


def test_no_interface_yaml_is_an_error(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    try:
        check_split.run(["--workspace", str(ws)])
        assert False, "expected CheckError"
    except CheckError:
        pass


def test_width_mismatch_caught(tmp_path):
    # gates.yaml's own named fault for this gate.
    analog = ANALOG_SPEC.replace(
        "    domain: clk_sys\n    width: 1\n",
        "    domain: clk_sys\n    width: 2\n", 1)
    ws = make_ws(tmp_path, analog=analog)
    payload, _out = check_split.run(["--workspace", str(ws)])
    assert payload["status"] == "violations"
    kinds = {v["kind"] for v in payload["violations"]}
    assert "width_mismatch" in kinds


def test_load_mismatch_caught(tmp_path):
    # `load` used to be read into canon/side dicts but never compared - a
    # differing load slipped through silently.
    analog = ANALOG_SPEC.replace(
        "    width: 1\n    load: 4pF\n",
        "    width: 1\n    load: 8pF\n", 1)
    ws = make_ws(tmp_path, analog=analog)
    payload, _out = check_split.run(["--workspace", str(ws)])
    assert payload["status"] == "violations"
    kinds = {v["kind"] for v in payload["violations"]}
    assert "load_mismatch" in kinds


def test_direction_mismatch_caught(tmp_path):
    digital = DIGITAL_SPEC.replace("direction: d2a", "direction: a2d")
    ws = make_ws(tmp_path, digital=digital)
    payload, _out = check_split.run(["--workspace", str(ws)])
    assert payload["status"] == "violations"
    kinds = {v["kind"] for v in payload["violations"]}
    assert "direction_mismatch" in kinds


def test_signal_missing_from_one_spec_caught(tmp_path):
    digital = """\
top: sensor_counted_digital
interface:
  - name: osc_out
    direction: a2d
    level: cmos_3v3
    domain: clk_free
    width: 1
"""
    ws = make_ws(tmp_path, digital=digital)
    payload, _out = check_split.run(["--workspace", str(ws)])
    assert payload["status"] == "violations"
    kinds = {v["kind"] for v in payload["violations"]}
    assert "signal_missing_from_spec" in kinds


def test_signal_not_declared_in_interface_caught(tmp_path):
    digital = DIGITAL_SPEC + """\
  - name: extra_sig
    direction: d2a
    level: cmos_3v3
    domain: clk_sys
    width: 4
"""
    ws = make_ws(tmp_path, digital=digital)
    payload, _out = check_split.run(["--workspace", str(ws)])
    assert payload["status"] == "violations"
    kinds = {v["kind"] for v in payload["violations"]}
    assert "signal_not_declared" in kinds


# ---------------------------------------------------------------- refusals
# Before the fix, an interface.yaml entry missing every field but `name`
# passed clean: canon and both sides read the same missing field as None,
# and None == None on direction, level, domain and width (load was not even
# compared). Each of these drives interface.yaml down to a bare `name` on
# one required field at a time and expects a refusal, not a pass.

def _interface_missing(field: str) -> str:
    lines = [ln for ln in INTERFACE.splitlines(keepends=True)
             if not ln.strip().startswith(f"{field}:")]
    return "".join(lines)


def _assert_missing_field_refused(tmp_path, field: str):
    ws = make_ws(tmp_path, interface=_interface_missing(field))
    try:
        check_split.run(["--workspace", str(ws)])
        assert False, f"expected CheckError for a signal missing {field!r}"
    except CheckError:
        pass


def test_missing_direction_is_refused(tmp_path):
    _assert_missing_field_refused(tmp_path, "direction")


def test_missing_level_is_refused(tmp_path):
    _assert_missing_field_refused(tmp_path, "level")


def test_missing_domain_is_refused(tmp_path):
    _assert_missing_field_refused(tmp_path, "domain")


def test_missing_width_is_refused(tmp_path):
    _assert_missing_field_refused(tmp_path, "width")


def test_missing_load_is_refused(tmp_path):
    _assert_missing_field_refused(tmp_path, "load")


def test_name_only_entry_is_refused(tmp_path):
    # The exact shape of the original bug: an entry with only a name.
    interface = """\
version: 1
signals:
  - name: osc_out
  - name: osc_en
    direction: d2a
    level: cmos_3v3
    domain: clk_sys
    width: 1
    load: 4pF
"""
    ws = make_ws(tmp_path, interface=interface)
    try:
        check_split.run(["--workspace", str(ws)])
        assert False, "expected CheckError"
    except CheckError:
        pass
