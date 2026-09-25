"""evals/ladder.py: the ladder.md rendering and the pieces of a run's score
that need no gate run (docs/design.md section 3, "### M6.")."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evals"))

import pytest  # noqa: E402

import ladder  # noqa: E402
from checklib import CheckError  # noqa: E402


def _result(rung, counts, **kw):
    r = {"skill": "vde", "rung": rung, "counts": counts, "gates_green": counts,
         "gate_problems": [] if counts else ["mutate: no fresh pass"],
         "held_out": {"status": "pass", "tests_passed": 1, "tests_run": 1},
         "hand_edits": 0, "scored_at": "2026-09-24T00:00:00+00:00",
         "kill_rate": 0.74, "area": 1234.5}
    r.update(kw)
    return r


def _write(results: Path, name: str, r: dict) -> None:
    (results / "ladder").mkdir(parents=True, exist_ok=True)
    (results / "ladder" / name).write_text(json.dumps(r), encoding="utf-8")


def test_render_has_all_four_vde_rungs_and_a_place_for_ade_and_msde(tmp_path):
    md = ladder.render(tmp_path / "results", tmp_path / "fixtures")
    assert "| level | vde | ade | msde |" in md
    for title in ("8-bit counter", "UART", "SPI peripheral with a FIFO",
                  "small RISC-V core"):
        assert title in md
    assert "## /ade" in md and "## /msde" in md
    # harder upward: level 4 is printed before level 1
    assert md.index("| 4 |") < md.index("| 1 |")


def test_render_takes_the_newest_result_per_rung(tmp_path):
    res = tmp_path / "results"
    _write(res, "2026-09-01T000000_vde_uart.json", _result("uart", False))
    _write(res, "2026-09-02T000000_vde_uart.json", _result("uart", True))
    md = ladder.render(res, tmp_path / "fixtures")
    assert "UART: counts" in md
    row = [ln for ln in md.splitlines() if ln.startswith("| uart |")][0]
    assert "| yes |" in row and "0.74" in row


def test_render_says_why_a_rung_is_red(tmp_path):
    res = tmp_path / "results"
    _write(res, "2026-09-02T000000_vde_uart.json",
           _result("uart", False, held_out={"status": "fail", "tests_passed": 0,
                                            "tests_run": 1}, hand_edits=2))
    row = [ln for ln in ladder.render(res, tmp_path / "f").splitlines()
           if ln.startswith("| uart |")][0]
    assert "1 gate(s) not green" in row and "held-out fails" in row
    assert "hand edits" in row


def test_worst_slack_is_the_minimum_setup_slack():
    corners = {"ss": {"setup_ws": -0.2, "hold_ws": 0.1},
               "tt": {"setup_ws": 1.5}, "bad": "x"}
    assert ladder.worst_slack(corners) == -0.2
    assert ladder.worst_slack(None) is None


def test_scoring_needs_hand_edits_declared(tmp_path):
    with pytest.raises(CheckError, match="--hand-edits"):
        ladder.run(["--skill", "vde", "--rung", "uart", "--run", str(tmp_path),
                    "--results-dir", str(tmp_path / "r")])


def test_render_shows_the_newest_cvdp_pass_rate_by_category(tmp_path):
    res = tmp_path / "results"
    (res / "cvdp").mkdir(parents=True)
    (res / "cvdp" / "2026-09-24T000000_nonagentic_null.json").write_text(json.dumps({
        "mode": "solutions", "subset_size": 277, "dataset_size": 302,
        "limit": 20, "caveat": "Not the official CVDP harness",
        "overall": {"pass": 3, "total": 20, "pass_rate": 0.15},
        "by_category": {"cid003": {"name": "spec to RTL", "pass": 3,
                                   "total": 8, "pass_rate": 0.375}}}))
    md = ladder.render(res, tmp_path / "f")
    assert "3 of 20 passed (0.15)" in md and "277 of 302" in md
    assert "| cid003 spec to RTL | 3 | 8 | 0.38 |" in md
    assert "Not the official CVDP harness" in md


def test_render_prefers_the_newest_full_cvdp_run_over_a_newer_limited_one(tmp_path):
    res = tmp_path / "results"
    (res / "cvdp").mkdir(parents=True)

    def write(name, limit, passed, total):
        (res / "cvdp" / name).write_text(json.dumps({
            "mode": "null", "subset_size": 277, "dataset_size": 302, "limit": limit,
            "dataset": {"file": ladder.CVDP_PINNED_FILE}, "categories": None,
            "selected": total,
            "overall": {"pass": passed, "total": total, "pass_rate": passed / total}}))

    write("2026-09-24T000000_nonagentic_null.json", None, 0, 277)
    write("2026-09-24T010000_nonagentic_null.json", 20, 2, 20)
    md = ladder.render(res, tmp_path / "f")
    assert "0 of 277 passed" in md and "2 of 20 passed" not in md
    # with no full run on record, the limited one is shown rather than nothing
    (res / "cvdp" / "2026-09-24T000000_nonagentic_null.json").unlink()
    assert "2 of 20 passed" in ladder.render(res, tmp_path / "f")


def test_a_newer_example_or_category_cvdp_run_is_not_the_full_subset_number(tmp_path):
    res = tmp_path / "results"
    (res / "cvdp").mkdir(parents=True)

    def write(name, **kw):
        c = {"mode": "null", "subset_size": 277, "dataset_size": 302, "limit": None,
             "dataset": {"file": ladder.CVDP_PINNED_FILE}, "categories": None,
             "selected": 277, "overall": {"pass": 0, "total": 277, "pass_rate": 0.0}}
        c.update(kw)
        (res / "cvdp" / name).write_text(json.dumps(c))

    write("2026-09-24T000000_nonagentic_null.json")
    # the README's positive control: the example set in reference mode
    write("2026-09-25T000000_example_reference.json", mode="reference",
          dataset={"file": "cvdp_v1.1.0_example_nonagentic_code_generation_no_commercial.jsonl"},
          subset_size=1, dataset_size=1, selected=1,
          overall={"pass": 1, "total": 1, "pass_rate": 1.0})
    # a --category run on the pinned file
    write("2026-09-25T010000_nonagentic_null.json", categories=["cid003"], selected=40,
          overall={"pass": 3, "total": 40, "pass_rate": 0.075})
    md = ladder.render(res, tmp_path / "f")
    assert "Newest full-subset run: `2026-09-24T000000_nonagentic_null.json`" in md
    assert "0 of 277 passed" in md
    assert "1 of 1 passed" not in md and "3 of 40 passed" not in md


def test_the_pinned_cvdp_file_matches_the_runner():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cvdp_run", REPO / "evals" / "cvdp" / "run.py")
    cvdp_run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cvdp_run)
    assert ladder.CVDP_PINNED_FILE == cvdp_run.DATASETS["nonagentic"]["file"]


def test_reference_runs_are_baselines_not_skill_results(tmp_path):
    res = tmp_path / "results"
    _write(res, "2026-09-02T000000_vde_counter8.json",
           _result("counter8", True, kind="reference", note="reference RTL"))
    _write(res, "2026-09-01T000000_vde_uart.json", _result("uart", False))
    md = ladder.render(res, tmp_path / "f")
    # the skill column and table ignore the reference run, even though it is newer
    assert "8-bit counter: not run" in md and "UART: red" in md
    vde = md.split("## /vde")[1].split("## /ade")[0]
    assert "| counter8 | not run |" in vde
    refs = md.split("## Reference baselines")[1]
    assert "| vde | counter8 | yes |" in refs and "uart" not in refs.split("## ")[0]
