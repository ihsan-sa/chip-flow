"""engine/scripts/bench.py: freezing, sha pins, stage gate lists, scoring and
the baseline comparison (docs/design.md section 3, "### M6."). Hermetic
pieces only; the real P4 bench on evals/fixtures/P4/uart_rtl runs its gates
for about three minutes and is marked slow."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "scripts"))
sys.path.insert(0, str(REPO / "engine" / "lib"))

import pytest  # noqa: E402

import bench  # noqa: E402
from checklib import CheckError  # noqa: E402


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "src" / "widget"
    for sub, name, text in (("spec", "spec.yaml", "top: widget\n"),
                            ("rtl", "widget.v", "module widget; endmodule\n"),
                            ("tb", "test_widget.py", "# req: R1\n"),
                            ("log", "run.log", "not an input\n")):
        (ws / sub).mkdir(parents=True, exist_ok=True)
        (ws / sub / name).write_text(text, encoding="utf-8")
    (ws / "state.json").write_text(json.dumps({"skill": "vde", "block": "widget"}),
                                   encoding="utf-8")
    return ws


def test_freeze_pins_inputs_and_leaves_history_out(tmp_path):
    fx = tmp_path / "fixtures" / "P4" / "widget_rtl"
    meta = bench.freeze(make_ws(tmp_path), fx, None, "P4")
    assert meta["skill"] == "vde" and meta["block"] == "widget"
    assert sorted(meta["files"]) == ["rtl/widget.v", "spec/spec.yaml",
                                     "tb/test_widget.py"]
    assert not (fx / "ws" / "log").exists()
    assert not (fx / "ws" / "state.json").exists()
    # never an absolute path in a committed fixture
    assert not Path(meta["source"]).is_absolute()
    assert bench.drift(fx, meta) == []


def test_drift_names_an_edited_a_missing_and_an_unpinned_file(tmp_path):
    fx = tmp_path / "fx"
    meta = bench.freeze(make_ws(tmp_path), fx, None, "P4")
    (fx / "ws" / "rtl" / "widget.v").write_text("module broken; endmodule\n")
    (fx / "ws" / "tb" / "test_widget.py").unlink()
    (fx / "ws" / "rtl" / "extra.v").write_text("\n")
    d = bench.drift(fx, meta)
    assert "rtl/widget.v: sha differs from its pin" in d
    assert "tb/test_widget.py: missing" in d
    assert "rtl/extra.v: not pinned" in d
    # the untouched spec is not reported
    assert not any(x.startswith("spec/") for x in d)


def test_freeze_refuses_what_is_neither_workspace_nor_rung(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(CheckError):
        bench.freeze(tmp_path / "empty", tmp_path / "fx", None, "P4")


def test_stage_gates_reads_the_phase_and_leaves_jobs_out():
    gates = {"vde": {"lint": {"phase": "P4"}, "synth": {"phase": "P5"},
                     "harden": {"phase": "P5", "job": True}}}
    assert bench.stage_gates("vde", "P5", gates) == ["synth"]
    assert bench.stage_gates("vde", "P3", gates) == ["sim", "mutate", "cover"]
    with pytest.raises(CheckError):
        bench.stage_gates("vde", "P9", gates)


def test_score_gate_partial_credit_only_for_a_failing_metric_gate():
    passed = {"status": "pass"}
    failed = {"status": "fail"}
    assert bench.score_gate("sim", {}, passed) == 1.0
    assert bench.score_gate("sim", {}, failed) == 0.0
    part = bench.score_gate("mutate", {"kill_rate": 0.45}, failed)
    assert 0.49 < part < 0.5
    # a failing metric gate never scores a full pass, even above threshold
    assert bench.score_gate("mutate", {"kill_rate": 0.95}, failed) < 1.0


def _score(composite, **statuses):
    return {"composite": composite,
            "gates": {g: {"status": s, "kinds": []} for g, s in statuses.items()}}


def test_compare_keeps_an_equal_score_and_flags_a_lower_one():
    base = _score(0.9, sim="pass", mutate="fail")
    assert bench.compare(_score(0.9, sim="pass", mutate="fail"), base) == []
    assert bench.compare(_score(0.95, sim="pass", mutate="fail"), base) == []
    lower = bench.compare(_score(0.8, sim="pass", mutate="fail"), base)
    assert len(lower) == 1 and "below the baseline" in lower[0]


def test_compare_flags_a_gate_that_stopped_passing_even_at_equal_composite():
    base = _score(0.5, sim="pass", mutate="fail")
    now = _score(0.5, sim="fail", mutate="pass")
    problems = bench.compare(now, base)
    assert problems == ["gate sim passed at baseline and now fails"]


def test_baseline_refuses_a_drifted_fixture(tmp_path):
    root = tmp_path / "fixtures"
    bench.freeze(make_ws(tmp_path), root / "P4" / "w", None, "P4")
    (root / "P4" / "w" / "ws" / "rtl" / "widget.v").write_text("x\n")
    with pytest.raises(CheckError, match="drifted"):
        bench.run(["--stage", "P4", "--fixture", "w", "--baseline",
                   "--fixtures-root", str(root),
                   "--results-dir", str(tmp_path / "res")])


def test_committed_fixtures_match_their_pins():
    for meta_p in (REPO / "evals" / "fixtures").glob("*/*/fixture.json"):
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        assert bench.drift(meta_p.parent, meta) == [], meta_p.parent.name
        assert (meta_p.parent / "baseline.json").is_file(), meta_p.parent.name


@pytest.mark.slow
def test_uart_rtl_compare_passes_against_its_baseline(tmp_path):
    payload, _ = bench.run(["--stage", "P4", "--fixture", "uart_rtl", "--compare",
                            "--results-dir", str(tmp_path)])
    assert payload["status"] == "pass", payload["violations"]


def _fake_bench(tmp_path, monkeypatch, reports):
    """bench() over a fake gate runner: `reports` maps gate -> report dict,
    or an exception to raise (a gate that did not run)."""
    fx = tmp_path / "fx"
    meta = bench.freeze(make_ws(tmp_path), fx, None, "P4")

    def fake_run(row, ws):
        r = reports[row["name"]]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(bench.gate, "run_report_for_gate", fake_run)
    gates = {"vde": {g: {"phase": "P4", "tool": g, "name": g} for g in reports}}
    return bench.bench(fx, meta, gates)


FAIL = {"violations": [{"severity": "error", "kind": "test_failed"}]}


def test_mutate_refusing_after_sim_failed_scores_zero_not_an_error(tmp_path, monkeypatch):
    score = _fake_bench(tmp_path, monkeypatch, {
        "sim": FAIL, "mutate": CheckError("unmutated design fails the visible tests")})
    assert score["gates"]["mutate"]["status"] == "not_run"
    assert score["composite"] == 0.0


def test_a_refusal_with_no_failed_precondition_is_still_an_error(tmp_path, monkeypatch):
    # formal refusing is not explained by a failing sim: exit 2, never a score
    with pytest.raises(CheckError, match="could not run"):
        _fake_bench(tmp_path, monkeypatch, {
            "sim": FAIL, "formal": CheckError("no formal properties")})
    with pytest.raises(CheckError, match="could not run"):
        _fake_bench(tmp_path, monkeypatch, {
            "sim": {"violations": []}, "mutate": CheckError("mcy missing")})
