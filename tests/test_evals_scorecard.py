"""evals/scorecard.py: the per-design scorecard, the suite score with its
intervals and the full-suite cost estimate, all from results on disk
(docs/design-evals.md sections 7 and 8)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evals"))

import pytest  # noqa: E402

import scorecard  # noqa: E402
from checklib import CheckError  # noqa: E402


def _vde(rung="uart", **kw):
    r = {"skill": "vde", "rung": rung, "kind": "skill", "phase": "P8",
         "counts": True, "gate_problems": [], "gates_passed": ["lint", "sim", "mutate"],
         "held_out": {"status": "pass"}, "kill_rate": 1.0, "line_pct": 100.0,
         "worst_slack_ns": 0.2, "area": 1000.0, "hand_edits": 0,
         "scored_at": "2026-10-01T00:00:00+00:00"}
    r.update(kw)
    return r


def _write(results: Path, name: str, r: dict) -> None:
    (results / "ladder").mkdir(parents=True, exist_ok=True)
    (results / "ladder" / name).write_text(json.dumps(r), encoding="utf-8")


def test_a_clean_vde_run_scores_one_on_every_area():
    c = scorecard.design_card(_vde(), {"area": 1200.0})
    assert c["composite"] == 1.0 and c["findings"] == []
    assert set(c["areas"]) == set(scorecard.AREAS)


def test_an_owed_area_never_measured_scores_zero_not_skipped():
    # stopped before STA and synth: implementation is owed, so it is 0
    c = scorecard.design_card(_vde(worst_slack_ns=None, area=None, line_pct=None), None)
    assert c["areas"]["implementation"] == 0.0
    assert c["areas"]["verification"] == 0.5  # kill rate 1.0, coverage missing -> 0
    # a measured one beside it keeps its score
    assert c["areas"]["function"] == 1.0


def test_area_is_scored_against_the_reference_and_negative_slack_fails():
    c = scorecard.design_card(_vde(area=2000.0, worst_slack_ns=-0.1), {"area": 1000.0})
    assert c["areas"]["implementation"] == pytest.approx(0.25)  # mean(0, 0.5)
    assert any(f["gate"] == "timing" and f["severity"] == "error" for f in c["findings"])


def test_analog_function_stays_unmeasured_and_composite_is_signoff_only():
    r = {"skill": "ade", "rung": "mirror", "kind": "skill", "counts": False,
         "gates_passed": ["sim_tt", "sim_pvt", "drc"],
         "gate_problems": ["lvs: no recorded result"], "held_out": {"status": "n/a"}}
    c = scorecard.design_card(r, None)
    assert c["areas"]["function"] is None
    assert c["areas"]["verification"] is None and c["areas"]["implementation"] is None
    assert c["composite"] == pytest.approx(0.75)  # 3 of 4 owed gates green


def test_findings_carry_a_fix_line_and_the_role_fix_dispatch_would_route_to():
    c = scorecard.design_card(_vde(counts=False, kill_rate=0.8, gate_problems=[
        "formal: last recorded result is FAIL", "drc: no recorded result"]), None)
    by_gate = {f["gate"]: f for f in c["findings"]}
    assert by_gate["formal"]["route_to"] == "property-writer"
    assert by_gate["mutate"]["route_to"] == "tb-writer"
    assert by_gate["mutate"]["severity"] == "warning"
    assert "run the drc gate (/vde drc)" in by_gate["drc"]["fix"]
    assert [f["severity"] for f in c["findings"]] == sorted(
        (f["severity"] for f in c["findings"]), key=["error", "warning", "info"].index)
    ade = scorecard.design_card({"skill": "ade", "rung": "mirror", "gate_problems":
                                 ["drc: no recorded result"], "held_out": {}}, None)
    assert ade["findings"][0]["route_to"] == "layout-fixer"


def test_a_failed_holdout_is_a_finding_that_names_no_test():
    c = scorecard.design_card(_vde(held_out={"status": "fail", "kinds": ["req_R3"]}), None)
    assert c["areas"]["function"] == 0.0
    f = [f for f in c["findings"] if f["gate"] == "holdout"][0]
    assert "req_R3" in f["what"] and "never the test" in f["fix"]


def test_process_is_reported_beside_the_score_never_in_it():
    clean = scorecard.design_card(_vde(), None)
    edited = scorecard.design_card(_vde(hand_edits=2, fix_attempts=9, cost_usd=50.0), None)
    assert edited["composite"] == clean["composite"]
    assert edited["process"]["hand_edits"] == 2
    assert any(f["severity"] == "info" for f in edited["findings"])


def test_suite_uses_skill_runs_only_with_seeded_intervals(tmp_path):
    res = tmp_path / "results"
    _write(res, "2026-10-01T000000_vde_uart.json", _vde("uart"))
    _write(res, "2026-10-01T000001_vde_counter8.json",
           _vde("counter8", counts=False, held_out={"status": "fail"}))
    _write(res, "2026-10-01T000002_vde_spi_fifo.json",
           _vde("spi_fifo", kind="reference", area=None))
    a = scorecard.build(res, 3)
    b = scorecard.build(res, 3)
    v = a["suite"]["vde"]
    assert v["scored"] == 2 and v["counted"] == 1
    assert v["composite_ci95"] == b["suite"]["vde"]["composite_ci95"]
    lo, hi = v["composite_ci95"]
    assert lo <= v["composite"] <= hi
    assert any(c["kind"] == "reference" for c in a["designs"])
    assert a["suite"]["ade"]["composite"] is None and a["suite"]["ade"]["scored"] == 0


def test_wilson_interval_bounds():
    assert scorecard.wilson(0, 0) is None
    lo, hi = scorecard.wilson(3, 3)
    assert hi == 1.0 and 0.4 < lo < 0.5
    lo, hi = scorecard.wilson(0, 2)
    assert lo == 0.0 and hi < 1.0


def test_cost_estimate_counts_every_costed_skill_run(tmp_path):
    res = tmp_path / "results"
    _write(res, "a_vde_uart.json", _vde(cost_usd=40.0, wall_s=3600))
    _write(res, "b_vde_uart.json", _vde(cost_usd=60.0, wall_s=7200))  # older run counts too
    _write(res, "c_vde_counter8.json", _vde("counter8", kind="reference", cost_usd=999.0))
    e = scorecard.build(res, 2)["cost_estimate"]
    assert e["runs"] == 12 * 3 * 2 * 2
    assert e["runs_with_cost"] == 2 and e["per_run_usd"]["max"] == 60.0
    assert e["suite_usd"]["median"] == 50 * e["runs"]
    assert e["one_rung_per_skill_runs"] == 3 * 3 * 2 * 2
    empty = scorecard.build(tmp_path / "none", 3)["cost_estimate"]
    assert empty["per_run_usd"] is None and empty["suite_usd"] is None
    assert "cannot be costed" in scorecard.render_cost(empty)


def test_doc_blocks_are_rewritten_between_markers_only(tmp_path):
    res = tmp_path / "results"
    _write(res, "2026-10-01T000000_vde_uart.json", _vde())
    doc = tmp_path / "d.md"
    doc.write_text("intro\n<!-- scorecard:begin -->\nold\n<!-- scorecard:end -->\nmid\n"
                   "<!-- cost:begin -->\n<!-- cost:end -->\ntail\n", encoding="utf-8")
    card = scorecard.build(res, 3)
    scorecard.rewrite_doc(doc, card)
    t = doc.read_text(encoding="utf-8")
    assert t.startswith("intro\n") and "\nmid\n" in t and t.endswith("tail\n")
    assert "old" not in t and "| vde/uart | skill |" in t and "design runs" in t
    row = [ln for ln in t.splitlines() if ln.startswith("| vde/uart |")][0]
    assert row.count("|") == 11  # ten cells, one per header column
    scorecard.rewrite_doc(doc, card)  # idempotent
    assert doc.read_text(encoding="utf-8") == t
    bare = tmp_path / "bare.md"
    bare.write_text("no markers\n", encoding="utf-8")
    with pytest.raises(CheckError):
        scorecard.rewrite_doc(bare, card)


def test_cli_records_a_dated_result(tmp_path, capsys):
    res = tmp_path / "results"
    _write(res, "2026-10-01T000000_vde_uart.json", _vde())
    assert scorecard.main(["--results-dir", str(res), "--record"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (res / "scorecard" / out["result_file"]).is_file()
    assert scorecard.main(["--results-dir", str(res), "--seeds", "0"]) == 2


# ---- --round: bare against skill, paired ------------------------------------

def _arm(arm, detail="typical", repeat=1, rung="counter8", **kw):
    return _vde(rung, kind="round", arm=arm, detail=detail, repeat=repeat,
                cost_usd=2.0 if arm == "skill" else 1.0,
                wall_s=600 if arm == "skill" else 300, **kw)


def _bad(arm, **kw):
    return _arm(arm, counts=False, kill_rate=0.0, gate_problems=["mutate: fail"], **kw)


def test_load_round_keeps_the_newest_armed_result_per_cell(tmp_path):
    _write(tmp_path, "2026-10-01T000000_a.json", _bad("bare"))
    _write(tmp_path, "2026-10-02T000000_a.json", _arm("bare"))
    _write(tmp_path, "2026-10-02T000000_noarm.json", _vde())  # no arm: not a round result
    (tmp_path / "notes.json").write_text("not json")
    got = scorecard.load_round(tmp_path)
    assert len(got) == 1 and got[0]["counts"] is True


def test_pairs_skill_minus_bare_and_lists_the_unpaired():
    rs = [_arm("skill", repeat=1), _bad("bare", repeat=1),
          _arm("skill", repeat=2), _arm("bare", repeat=2),
          _arm("skill", repeat=3)]  # no bare partner
    rep = scorecard.round_report(rs, {})
    v = rep["skills"]["vde"]
    assert v["pairs"] == 2
    pooled = v["skill_minus_bare"]["pooled"]
    assert pooled["counts"]["mean"] == 0.5 and pooled["counts"]["n"] == 2
    assert set(v["skill_minus_bare"]["by_detail"]) == {"typical"}
    assert rep["unpaired"] == [{"skill": "vde", "rung": "counter8", "detail": "typical",
                                "repeat": 3, "has": "skill"}]
    assert v["arms"]["skill"]["runs"] == 3 and v["arms"]["bare"]["runs"] == 2
    assert v["arms"]["skill"]["cost_usd"] == {"n": 3, "mean": 2.0, "total": 6.0}
    assert v["arms"]["bare"]["wall_s"]["total"] == 600
    assert v["arms"]["bare"]["counted"] == 1


def test_cluster_bootstrap_is_seeded_and_resamples_rungs_then_pairs():
    two = {"counter8": [1.0, 0.0, 1.0], "uart": [0.0, 0.0]}
    a, b = scorecard.cluster_bootstrap(two), scorecard.cluster_bootstrap(two)
    assert a == b and 0.0 <= a[0] <= a[1] <= 1.0
    # rungs are resampled first: two rungs that disagree completely give the
    # full range, where resampling the six pooled pairs would not reach 0 or 1
    assert scorecard.cluster_bootstrap({"a": [1.0] * 3, "b": [0.0] * 3}) == [0.0, 1.0]
    assert scorecard.cluster_bootstrap({"counter8": [0.5, 0.5]}) == [0.5, 0.5]
    assert scorecard.cluster_bootstrap({}) is None
    assert scorecard.cluster_bootstrap({"x": []}) is None


def test_detail_steps_pair_levels_within_an_arm():
    rs = [_bad("skill", detail="terse"), _arm("skill", detail="typical"),
          _arm("skill", detail="full"), _arm("bare", detail="typical")]
    steps = scorecard.round_report(rs, {})["skills"]["vde"]["detail_steps"]
    assert steps["skill"]["typical-terse"]["counts"]["mean"] == 1.0
    assert steps["skill"]["full-typical"]["counts"]["mean"] == 0.0
    assert "bare" not in steps  # one level only: no step to take


def test_not_hardened_scores_zero_on_implementation_with_a_fix_line():
    hardened = scorecard.design_card(_arm("bare", hardened=True), {"area": 1200.0})
    assert hardened["areas"]["implementation"] == 1.0
    bare = _arm("bare", hardened=False, worst_slack_ns=None,
                gate_problems=["harden: not hardened", "sim: no testbench"])
    c = scorecard.design_card(bare, {"area": 1200.0})
    assert c["areas"]["implementation"] == 0.0
    fixes = " ".join(f["fix"] for f in c["findings"])
    assert "harden/runs/run/final/" in fixes and "tb/test_*.py" in fixes


def test_cli_round_writes_json_and_markdown(tmp_path, capsys):
    rd = tmp_path / "round"
    for i, r in enumerate([_arm("skill"), _bad("bare"), _arm("skill", detail="terse")]):
        _write(rd, f"2026-10-02T00000{i}_x.json", r)
    assert scorecard.main(["--round", str(rd), "--results-dir", str(tmp_path / "res")]) == 0
    rep = json.loads((rd / "round-scorecard.json").read_text())
    assert rep["skills"]["vde"]["pairs"] == 1 and len(rep["runs"]) == 3
    md = (rd / "round-scorecard.md").read_text()
    assert "## vde: skill minus bare" in md and "| pooled | 1 |" in md
    assert "Unpaired" in md and "terse r1 (skill only)" in md
    # same results, same report
    assert scorecard.main(["--round", str(rd), "--results-dir", str(tmp_path / "res"),
                           "--out", str(tmp_path / "again.json"),
                           "--md", str(tmp_path / "again.md")]) == 0
    assert (tmp_path / "again.md").read_text() == md
    again = json.loads((tmp_path / "again.json").read_text())
    assert again["skills"] == rep["skills"]
    # an empty round is an error, not an empty pass
    (tmp_path / "empty").mkdir()
    assert scorecard.main(["--round", str(tmp_path / "empty")]) == 2
