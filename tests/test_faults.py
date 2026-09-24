"""engine/scripts/faults.py: manifest loading, scratch workspace assembly,
plant-script import and expect-matching (docs/design.md section 3, "###
M2."). Fast/hermetic pieces only - the real corpus end-to-end run (every
gate, including the slow mcy-driven mutate gate, twice per rung) is tests/
smoke-faults.sh, not part of this suite."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import faults  # noqa: E402
from checklib import CheckError  # noqa: E402

MANIFEST = """\
version: 1
faults:
  - name: a_fault
    gate: lint
    plant: plant_a.py
    expect: {status: fail, kind: LATCH}
"""


def make_rung(tmp_path: Path, manifest_text: str | None = None) -> Path:
    rung = tmp_path / "corpus" / "vde" / "widget"
    (rung / "rtl").mkdir(parents=True)
    (rung / "tb").mkdir(parents=True)
    (rung / "faults").mkdir(parents=True)
    (rung / "spec.md").write_text("# widget\n", encoding="utf-8")
    (rung / "spec.yaml").write_text("top: widget\nrequirements: []\n",
                                    encoding="utf-8")
    (rung / "rtl" / "widget.v").write_text("module widget; endmodule\n",
                                           encoding="utf-8")
    (rung / "tb" / "test_widget.py").write_text("# a test module\n",
                                                encoding="utf-8")
    if manifest_text is not None:
        (rung / "faults" / "manifest.yaml").write_text(manifest_text,
                                                        encoding="utf-8")
    return rung


def test_load_manifest_missing_file_raises(tmp_path):
    rung = make_rung(tmp_path)
    with pytest.raises(CheckError):
        faults.load_manifest(rung)


def test_load_manifest_empty_list_raises(tmp_path):
    rung = make_rung(tmp_path, "version: 1\nfaults: []\n")
    with pytest.raises(CheckError):
        faults.load_manifest(rung)


def test_load_manifest_reads_real_entries(tmp_path):
    rung = make_rung(tmp_path, MANIFEST)
    data = faults.load_manifest(rung)
    assert data["faults"][0]["gate"] == "lint"


def test_make_scratch_workspace_nests_flat_spec_under_spec_dir(tmp_path):
    rung = make_rung(tmp_path, MANIFEST)
    tmp_root = tmp_path / "scratch"
    tmp_root.mkdir()
    ws = faults.make_scratch_workspace(tmp_root, rung, "vde", "widget")
    assert (ws / "spec" / "spec.md").is_file()
    assert (ws / "spec" / "spec.yaml").is_file()
    assert (ws / "rtl" / "widget.v").is_file()
    assert (ws / "tb" / "test_widget.py").is_file()
    assert (ws / "state.json").is_file()


def test_make_scratch_workspace_is_a_fresh_copy_each_time(tmp_path):
    rung = make_rung(tmp_path, MANIFEST)
    tmp_root = tmp_path / "scratch"
    tmp_root.mkdir()
    ws1 = faults.make_scratch_workspace(tmp_root, rung, "vde", "widget")
    (ws1 / "rtl" / "widget.v").write_text("mutated", encoding="utf-8")
    ws2 = faults.make_scratch_workspace(tmp_root, rung, "vde", "widget")
    assert ws2 == ws1
    assert (ws2 / "rtl" / "widget.v").read_text(encoding="utf-8") != "mutated"


def test_check_expect_matches():
    result = {"status": "fail", "failing": [{"kind": "LATCH"}, {"kind": "X"}]}
    assert faults.check_expect(
        "f", result, {"status": "fail", "kind": "LATCH"}) == []


def test_check_expect_reports_status_mismatch():
    result = {"status": "pass", "failing": []}
    problems = faults.check_expect("f", result, {"status": "fail"})
    assert len(problems) == 1
    assert "expected gate status" in problems[0]


def test_check_expect_reports_kind_mismatch():
    result = {"status": "fail", "failing": [{"kind": "OTHER"}]}
    problems = faults.check_expect(
        "f", result, {"status": "fail", "kind": "LATCH"})
    assert len(problems) == 1
    assert "kind" in problems[0]


def test_import_plant_missing_script_raises(tmp_path):
    rung = make_rung(tmp_path, MANIFEST)
    with pytest.raises(CheckError):
        faults.import_plant(rung, "nope.py")


def test_import_plant_no_plant_function_raises(tmp_path):
    rung = make_rung(tmp_path, MANIFEST)
    (rung / "faults" / "bad.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(CheckError):
        faults.import_plant(rung, "bad.py")


def test_import_plant_calls_the_real_function(tmp_path):
    rung = make_rung(tmp_path, MANIFEST)
    (rung / "faults" / "good.py").write_text(
        "def plant(ws):\n    (ws / 'marker.txt').write_text('planted')\n",
        encoding="utf-8")
    plant = faults.import_plant(rung, "good.py")
    plant(tmp_path)
    assert (tmp_path / "marker.txt").read_text() == "planted"


HOLDOUT_MANIFEST = """\
version: 1
faults:
  - name: h
    gate: holdout
    plant: plant_h.py
    expect: {status: fail, kind: holdout_failed}
"""


def make_holdout_rung(tmp_path: Path) -> Path:
    rung = make_rung(tmp_path, HOLDOUT_MANIFEST)
    (rung / "holdout").mkdir()
    (rung / "holdout" / "test_widget_holdout.py").write_text(
        "# a holdout module\n", encoding="utf-8")
    (rung / "faults" / "plant_h.py").write_text(
        "def plant(ws):\n    pass\n", encoding="utf-8")
    return rung


def test_run_rung_flags_a_holdout_fault_that_also_breaks_sim(tmp_path, monkeypatch):
    # a holdout fault must be invisible to tb/ - that is the whole point of
    # the gate (gates.yaml: "... which the visible tests do not look"). One
    # that also fails sim demonstrates the wrong thing and must be flagged,
    # even though its own `expect` (holdout/holdout_failed) is satisfied.
    rung = make_holdout_rung(tmp_path)
    calls: list[str] = []

    def fake_run_gate(ws, gate_name, gates, skill):
        calls.append(gate_name)
        if gate_name == "holdout" and calls.count("holdout") == 1:
            return {}, {"status": "pass", "failing": []}   # clean reference
        if gate_name == "holdout":
            return {}, {"status": "fail",
                       "failing": [{"kind": "holdout_failed"}]}
        assert gate_name == "sim"
        return {}, {"status": "fail", "failing": [{"kind": "test_failed"}]}

    monkeypatch.setattr(faults, "run_gate", fake_run_gate)
    tmp_root = tmp_path / "scratch"
    tmp_root.mkdir()
    result = faults.run_rung(tmp_root, rung, "vde", gates={})
    assert calls == ["holdout", "holdout", "sim"]
    assert any("must leave the sim gate passing" in p for p in result["problems"])


def test_run_rung_does_not_flag_a_holdout_fault_that_leaves_sim_passing(
        tmp_path, monkeypatch):
    rung = make_holdout_rung(tmp_path)
    calls: list[str] = []

    def fake_run_gate(ws, gate_name, gates, skill):
        calls.append(gate_name)
        if gate_name == "holdout" and calls.count("holdout") == 1:
            return {}, {"status": "pass", "failing": []}
        if gate_name == "holdout":
            return {}, {"status": "fail",
                       "failing": [{"kind": "holdout_failed"}]}
        assert gate_name == "sim"
        return {}, {"status": "pass", "failing": []}

    monkeypatch.setattr(faults, "run_gate", fake_run_gate)
    tmp_root = tmp_path / "scratch"
    tmp_root.mkdir()
    result = faults.run_rung(tmp_root, rung, "vde", gates={})
    assert calls == ["holdout", "holdout", "sim"]
    assert result["problems"] == []


def test_run_rung_does_not_check_sim_for_a_non_holdout_fault(tmp_path, monkeypatch):
    rung = make_rung(tmp_path, MANIFEST)   # MANIFEST's own fault is a `lint` gate
    (rung / "faults" / "plant_a.py").write_text(
        "def plant(ws):\n    pass\n", encoding="utf-8")
    calls: list[str] = []

    def fake_run_gate(ws, gate_name, gates, skill):
        calls.append(gate_name)
        return {}, {"status": "fail", "failing": [{"kind": "LATCH"}]}

    monkeypatch.setattr(faults, "run_gate", fake_run_gate)
    tmp_root = tmp_path / "scratch"
    tmp_root.mkdir()
    faults.run_rung(tmp_root, rung, "vde", gates={})
    assert "sim" not in calls
