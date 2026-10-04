"""evals/ladder.py: the ladder.md rendering and the pieces of a run's score
that need no gate run (docs/design.md section 3, "### M6.")."""
from __future__ import annotations

import json
import shutil
import subprocess
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


# --- rulings: the owner's per-mutant rulings are their own field --------------

RULINGS = """equivalent:
  - id: 12
    ruling: "owner, 2026-09-25"
    evidence: "no output can observe it"
"""


def _ruled_ws(tmp_path: Path, digital: int = 1, analog: int = 1) -> Path:
    """A /msde run with one rulings file per nested side, plus a stray copy
    under runs/ that must not be counted."""
    ws = tmp_path / "sensor_counted"
    (ws / "runs" / "x" / "spec").mkdir(parents=True)
    (ws / "runs" / "x" / "spec" / "mutant_rulings.yaml").write_text(RULINGS)
    for side, n in (("digital", digital), ("analog", analog)):
        (ws / side / "spec").mkdir(parents=True)
        (ws / side / "spec" / "mutant_rulings.yaml").write_text(
            "equivalent:\n" + "".join(
                f"  - {{id: {i}, ruling: owner, evidence: why}}\n" for i in range(n))
            if n else "")
    (ws / "state.json").write_text(json.dumps({"skill": "msde", "block": "sensor_counted"}))
    return ws


def _score(monkeypatch, ws: Path, rulings: int, hand_edits: int = 0):
    monkeypatch.setattr(ladder, "gates_green", lambda ws, data: [])
    monkeypatch.setattr(ladder, "run_holdout", lambda ws, skill, rd: {"status": "n/a"})
    return ladder.run(["--skill", "msde", "--rung", "sensor_counted", "--run", str(ws),
                       "--hand-edits", str(hand_edits), "--rulings", str(rulings),
                       "--results-dir", str(ws.parent / "r"),
                       "--ladder-md", str(ws.parent / "ladder.md")])[0]


def test_count_rulings_sums_every_nested_side_but_not_runs(tmp_path):
    assert ladder.count_rulings(_ruled_ws(tmp_path, 2, 1)) == 3
    assert ladder.count_rulings(_ruled_ws(tmp_path / "b", 0, 0)) == 0


def test_declared_rulings_are_recorded_and_the_rung_still_counts(tmp_path, monkeypatch):
    out = _score(monkeypatch, _ruled_ws(tmp_path), rulings=2)
    assert out["status"] == "pass" and out["result"]["counts"] is True
    assert out["result"]["rulings"] == 2 and out["result"]["rulings_undeclared"] == 0
    assert out["result"]["hand_edits"] == 0
    row = [ln for ln in (tmp_path / "ladder.md").read_text().splitlines()
           if ln.startswith("| sensor_counted |")][0]
    assert row.startswith("| sensor_counted | yes |  | n/a | 2 |")


def test_undeclared_rulings_stop_the_rung_like_hand_edits(tmp_path, monkeypatch):
    out = _score(monkeypatch, _ruled_ws(tmp_path), rulings=1)
    assert out["result"]["counts"] is False
    assert out["result"]["rulings"] == 1 and out["result"]["rulings_undeclared"] == 1
    assert [v["kind"] for v in out["violations"]] == ["rulings_undeclared"]
    row = [ln for ln in (tmp_path / "ladder.md").read_text().splitlines()
           if ln.startswith("| sensor_counted |")][0]
    assert "| no | undeclared rulings |" in row


def test_hand_edits_still_stop_a_ruled_rung(tmp_path, monkeypatch):
    out = _score(monkeypatch, _ruled_ws(tmp_path), rulings=2, hand_edits=1)
    assert out["result"]["counts"] is False
    assert [v["kind"] for v in out["violations"]] == ["hand_edits"]


def test_declaring_more_rulings_than_the_run_holds_is_an_error(tmp_path, monkeypatch):
    with pytest.raises(CheckError, match="hold 2 entries"):
        _score(monkeypatch, _ruled_ws(tmp_path), rulings=3)


def test_an_old_result_without_rulings_renders_a_dash(tmp_path):
    res = tmp_path / "results"
    _write(res, "2026-09-02T000000_vde_uart.json", _result("uart", True))
    row = [ln for ln in ladder.render(res, tmp_path / "f").splitlines()
           if ln.startswith("| uart |")][0]
    assert row.startswith("| uart | yes |  | pass (1/1) | - | 0.74 |")


# ---- --deliverables: a bare-against-skill round arm --------------------------

CORPUS8 = REPO / "corpus" / "vde" / "counter8"


def _arm(root: Path, *, tb: bool = True, final: bool = False) -> Path:
    """An arm's deliverables: the corpus counter8 RTL (and tb), optionally a
    stand-in final/ holding every view check_harden expects."""
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "counter8.v").write_text((CORPUS8 / "rtl" / "counter8.v").read_text())
    if tb:
        (root / "tb").mkdir()
        (root / "tb" / "test_counter8.py").write_text(
            (CORPUS8 / "tb" / "test_counter8.py").read_text())
    if final:
        f = root / ladder.HARDEN_FINAL
        for v in ("gds", "lef", "nl", "sdf", "spef"):
            (f / v).mkdir(parents=True)
            (f / v / f"tt_um_counter8.{v}").write_text("x")
        (f / "metrics.json").write_text(json.dumps({"design__instance__count": 9}))
    return root


def test_copy_regular_keeps_files_and_skips_every_link(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "sub").mkdir(parents=True)
    (src / "a.v").write_text("module a; endmodule\n")
    (src / "a.v").chmod(0o755)
    (src / "sub" / "b.v").write_text("b")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_text("noise")
    secret = tmp_path / "secret"
    (secret / "holdout").mkdir(parents=True)
    (secret / "holdout" / "test_x.py").write_text("held out")
    (src / "file_link.py").symlink_to(secret / "holdout" / "test_x.py")
    (src / "dir_link").symlink_to(secret / "holdout", target_is_directory=True)
    (src / "dangling").symlink_to(tmp_path / "nowhere")
    skipped: list[dict] = []
    n = ladder.copy_regular(src, dst, "tb", skipped)
    assert n == 2
    assert (dst / "a.v").read_text() == "module a; endmodule\n"
    assert (dst / "a.v").stat().st_mode & 0o111 == 0  # exec bit dropped
    assert (dst / "sub" / "b.v").is_file()
    assert not (dst / "__pycache__").exists()
    assert not (dst / "file_link.py").exists() and not (dst / "dir_link").exists()
    assert {s["path"]: s["why"] for s in skipped} == {
        "tb/file_link.py": "symlink", "tb/dir_link": "symlink", "tb/dangling": "symlink"}


def test_find_deliverables_root_prefers_root_then_named_block(tmp_path):
    root = tmp_path / "root"
    (root / "rtl").mkdir(parents=True)
    (root / "blocks" / "counter8" / "rtl").mkdir(parents=True)
    assert ladder.find_deliverables_root(root, ["counter8"]) == (root, ".")

    blk = tmp_path / "blk"
    (blk / "blocks" / "other" / "rtl").mkdir(parents=True)
    (blk / "blocks" / "counter8" / "rtl").mkdir(parents=True)
    assert ladder.find_deliverables_root(blk, ["counter8"]) == (
        blk / "blocks" / "counter8", "blocks/counter8")

    only = tmp_path / "only"
    (only / "blocks" / "mine" / "rtl").mkdir(parents=True)
    assert ladder.find_deliverables_root(only, ["counter8"])[1] == "blocks/mine"

    two = tmp_path / "two"
    for b in ("x", "y"):
        (two / "blocks" / b / "rtl").mkdir(parents=True)
    assert ladder.find_deliverables_root(two, ["counter8"]) == (two, ".")


def test_find_deliverables_root_never_follows_a_link(tmp_path):
    real = tmp_path / "real"
    (real / "rtl").mkdir(parents=True)
    ws = tmp_path / "ws"
    (ws / "blocks" / "counter8").mkdir(parents=True)
    (ws / "rtl").symlink_to(real / "rtl", target_is_directory=True)
    (ws / "blocks" / "counter8" / "rtl").symlink_to(real / "rtl", target_is_directory=True)
    assert ladder.find_deliverables_root(ws, ["counter8"]) == (ws, ".")
    link_ws = tmp_path / "link_ws"
    link_ws.symlink_to(real, target_is_directory=True)
    with pytest.raises(CheckError, match="not a real directory"):
        ladder.find_deliverables_root(link_ws, ["counter8"])


def test_build_scoring_ws_skips_a_linked_harden_tree_and_never_copies_arm_spec(tmp_path):
    arm = _arm(tmp_path / "arm")
    (arm / "spec").mkdir()
    (arm / "spec" / "mutant_rulings.yaml").write_text("equivalent: []\n")
    elsewhere = _arm(tmp_path / "elsewhere", final=True)
    (arm / "harden").symlink_to(elsewhere / "harden", target_is_directory=True)
    ws = tmp_path / "score" / "counter8"
    info = ladder.build_scoring_ws(ws, CORPUS8, arm, "counter8")
    assert info["copied"] == {"rtl": 1, "tb": 1, str(ladder.HARDEN_FINAL): 0}
    assert info["skipped"] == [{"path": "harden", "why": "symlink"}]
    assert not (ws / ladder.HARDEN_FINAL).exists()
    assert not (ws / "spec" / "mutant_rulings.yaml").exists()
    assert (ws / "spec" / "spec.yaml").read_text() == (CORPUS8 / "spec.yaml").read_text()
    assert (ws / "holdout" / "test_counter8_holdout.py").is_file()

    # the same tree as real directories is copied
    arm2 = _arm(tmp_path / "arm2", final=True)
    info2 = ladder.build_scoring_ws(tmp_path / "score2" / "counter8", CORPUS8, arm2, "counter8")
    assert info2["copied"][str(ladder.HARDEN_FINAL)] == 6 and info2["skipped"] == []


def _fake_gates(monkeypatch, calls: list, on_gate=None):
    def fake(ws, gate, outdir, report=None):
        calls.append(gate)
        if on_gate:
            on_gate(ws, gate)
        if report is not None:
            body = json.loads(report.read_text())
            return {"status": "pass" if not body.get("violations") else "fail",
                    "facts": {}}
        facts = {"mutate": {"kill_rate": 0.9}, "cover": {"line_pct": 95.0},
                 "synth": {"area": 100.0},
                 "holdout": {"tests_passed": 3, "tests_run": ["a", "b", "c"]},
                 "timing": {"corners": {"ss": {"setup_ws": 0.5}}}}.get(gate, {})
        return {"status": "pass", "wall_s": 0.0, "failing": 0, "kinds": [],
                "facts": facts}
    monkeypatch.setattr(ladder, "run_gate", fake)
    monkeypatch.setattr(ladder, "sandbox_preflight",
                        lambda ws, outdir: {"bwrap": "fake"})
    monkeypatch.setattr(ladder, "_git_head", lambda: "abc123")
    monkeypatch.setattr(ladder, "_tool_image", lambda: "iic-osic-tools-test")


def _deliv(tmp_path, ws, *extra):
    return ladder.run(["--skill", "vde", "--rung", "counter8", "--deliverables", str(ws),
                       "--hand-edits", "0", "--results-dir", str(tmp_path / "r"),
                       "--ladder-md", str(tmp_path / "ladder.md"), *extra])[0]


def test_a_hardened_arm_with_every_gate_green_counts(tmp_path, monkeypatch):
    calls: list = []
    _fake_gates(monkeypatch, calls)
    arm = _arm(tmp_path / "arm", final=True)
    out = _deliv(tmp_path, arm, "--arm", "bare", "--detail", "terse", "--repeat", "3",
                 "--model", "m-1")
    r = out["result"]
    assert out["status"] == "pass" and r["counts"] is True and r["integrity"] == "ok"
    assert r["kind"] == "round" and r["scoring"] == "deliverables"
    assert (r["arm"], r["detail"], r["repeat"], r["model"]) == ("bare", "terse", 3, "m-1")
    assert r["chip_flow_commit"] == "abc123" and r["tool_image"] == "iic-osic-tools-test"
    assert r["hardened"] is True and r["testbench"] is True
    assert r["held_out"] == {"status": "pass", "tests_passed": 3, "tests_run": 3,
                             "kinds": []}
    assert (r["kill_rate"], r["line_pct"], r["area"], r["worst_slack_ns"]) == (
        0.9, 95.0, 100.0, 0.5)
    # corpus-graded gates before any gate that runs the arm's testbench; release last
    assert calls[-1] == "release"
    assert max(calls.index(g) for g in ("holdout", "formal", "harden")) < min(
        calls.index(g) for g in ("sim", "mutate", "cover", "glsim"))
    assert out["result_file"].endswith("_vde_counter8_bare_terse_r3.json")
    assert (tmp_path / "r" / "ladder" / out["result_file"]).is_file()
    assert "## Bare against skill rounds" in (tmp_path / "ladder.md").read_text()


def test_an_arm_with_no_gds_and_no_testbench_does_not_count(tmp_path, monkeypatch):
    calls: list = []
    _fake_gates(monkeypatch, calls)
    out = _deliv(tmp_path, _arm(tmp_path / "arm", tb=False), "--no-regen")
    r = out["result"]
    assert r["counts"] is False and r["hardened"] is False and r["testbench"] is False
    for g in ("harden", "timing", "drc", "lvs", "precheck"):
        assert r["gates"][g]["status"] == "not hardened" and g not in calls
    for g in ("sim", "mutate", "cover"):
        assert r["gates"][g]["status"] == "no testbench" and g not in calls
    assert r["gates"]["glsim"]["status"] == "not hardened"
    assert "harden: not hardened" in r["gate_problems"]
    assert "sim: no testbench" in r["gate_problems"]
    assert "holdout" in calls and "lint" in calls
    assert not (tmp_path / "ladder.md").exists()  # --no-regen


def test_a_deliverable_changed_mid_scoring_stops_the_rest(tmp_path, monkeypatch):
    def tamper(ws, gate):
        if gate == "lint":
            (ws / "holdout" / "test_counter8_holdout.py").write_text("passes\n")
    calls: list = []
    _fake_gates(monkeypatch, calls, tamper)
    r = _deliv(tmp_path, _arm(tmp_path / "arm", final=True), "--no-regen")["result"]
    assert r["integrity"] == ["holdout/test_counter8_holdout.py"]
    assert r["counts"] is False and calls[-1] == "lint"
    assert r["gates"]["holdout"]["status"] == "not run"
    assert any(p.startswith("integrity:") for p in r["gate_problems"])


def test_cache_noise_a_gate_writes_is_not_tampering(tmp_path, monkeypatch):
    def noise(ws, gate):
        (ws / "tb" / "__pycache__").mkdir(exist_ok=True)
        (ws / "tb" / "__pycache__" / f"{gate}.pyc").write_text("x")
    _fake_gates(monkeypatch, [], noise)
    r = _deliv(tmp_path, _arm(tmp_path / "arm", final=True), "--no-regen")["result"]
    assert r["integrity"] == "ok" and r["counts"] is True


def test_deliverables_refuses_run_reference_rulings_and_analog(tmp_path, monkeypatch):
    _fake_gates(monkeypatch, [])
    arm = _arm(tmp_path / "arm")
    with pytest.raises(CheckError, match="not both"):
        _deliv(tmp_path, arm, "--run", str(arm))
    with pytest.raises(CheckError, match="--reference"):
        _deliv(tmp_path, arm, "--reference")
    with pytest.raises(CheckError, match="--rulings must be 0"):
        _deliv(tmp_path, arm, "--rulings", "1")
    with pytest.raises(CheckError, match="vde rungs only"):
        ladder.run(["--skill", "ade", "--rung", "counter8", "--deliverables", str(arm),
                    "--hand-edits", "0", "--results-dir", str(tmp_path / "r")])


def test_round_results_get_their_own_section_never_a_skill_column(tmp_path):
    res = tmp_path / "results"
    _write(res, "2026-09-02T000000_vde_counter8_bare_typical_r1.json",
           _result("counter8", True, kind="round", arm="bare", detail="typical",
                   repeat=1, model="m-1", hardened=False, testbench=True))
    _write(res, "2026-09-02T000000_vde_uart.json", _result("uart", True))
    md = ladder.render(res, tmp_path / "f")
    assert "## Bare against skill rounds" in md
    row = [ln for ln in md.splitlines() if ln.startswith("| vde/counter8 | bare |")][0]
    assert "| typical | 1 | m-1 | yes |" in row and "| not hardened |" in row
    assert not [ln for ln in md.splitlines() if ln.startswith("| counter8 | yes")]
    # with no round result the section is left out
    assert "## Bare against skill rounds" not in ladder.render(
        tmp_path / "empty", tmp_path / "f")


def test_result_name_adds_arm_detail_repeat_only_when_needed():
    import argparse
    a = argparse.Namespace(skill="vde", rung="uart", deliverables=None, repeat=None,
                           arm="skill", detail="typical")
    assert ladder.result_name(a, "S") == "S_vde_uart.json"
    a.repeat = 2
    assert ladder.result_name(a, "S") == "S_vde_uart_skill_typical_r2.json"
    a.repeat, a.deliverables = None, "ws"
    assert ladder.result_name(a, "S") == "S_vde_uart_skill_typical_r0.json"


@pytest.mark.slow
def test_corpus_counter8_scores_through_deliverables_with_real_gates(tmp_path):
    """Real tools: the corpus RTL and tb, no GDS. Everything up to the
    hardened gates passes; the hardened ones read "not hardened"."""
    out = _deliv(tmp_path, _arm(tmp_path / "arm"), "--no-regen")
    r = out["result"]
    for g in ("spec_lint", "lint", "holdout", "formal", "synth", "sim", "mutate", "cover"):
        assert r["gates"][g]["status"] == "pass", (g, r["gates"][g])
    assert r["gates"]["harden"]["status"] == "not hardened"
    assert r["integrity"] == "ok" and r["counts"] is False


# ---- the scoring sandbox ------------------------------------------------------

needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="no bwrap")
REF_RTL = CORPUS8 / "rtl" / "counter8.v"


def _sandbox_run(ws: Path, out: Path, script: str) -> subprocess.CompletedProcess:
    return subprocess.run(ladder.gate_sandbox_argv(ws, out, ["/bin/sh", "-c", script]),
                          capture_output=True, text=True, timeout=120)


def test_the_gate_sandbox_argv_hides_the_corpus_and_shares_nothing(tmp_path):
    (tmp_path / "tree").mkdir()
    argv = ladder.gate_sandbox_argv(tmp_path / "ws", tmp_path / "out", ["true"],
                                    tree=tmp_path / "tree")
    head = argv[:argv.index("--")]
    assert head[0] == "bwrap" and "--unshare-all" in head and "--clearenv" in head
    assert "--die-with-parent" in head
    assert not any(a.startswith("--share") for a in head)
    pairs = {(head[i], head[i + 1]) for i in range(len(head) - 1)}
    for m in ladder.SANDBOX_MASKED:
        assert ("--tmpfs", str(ladder.REPO / m)) in pairs
    i = head.index(str(ladder.REPO))
    assert head[i - 1] == "--ro-bind"  # the repo is read-only
    assert ("--bind", str(tmp_path / "ws")) in pairs
    assert ("--ro-bind", str(tmp_path / "tree")) in pairs
    envs = {head[i + 1]: head[i + 2] for i in range(len(head)) if head[i] == "--setenv"}
    assert envs["HOME"] == ladder.SB_SCORE_HOME and envs["EDA_TOOLCHAIN"] == str(
        tmp_path / "tree")


def test_scoring_without_bwrap_refuses_with_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ladder.shutil, "which", lambda name: None)
    with pytest.raises(CheckError, match="bwrap"):
        ladder.sandbox_preflight(tmp_path, tmp_path)
    rc = ladder.main(["--skill", "vde", "--rung", "counter8", "--deliverables",
                      str(_arm(tmp_path / "arm")), "--hand-edits", "0",
                      "--results-dir", str(tmp_path / "r"), "--no-regen"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error" and "bwrap" in out["remediation"]
    assert not (tmp_path / "r").exists()


@needs_bwrap
def test_the_gate_sandbox_blocks_the_reference_and_the_host(tmp_path):
    """The real sandbox: the corpus reference is missing by its absolute
    path and nothing outside the scoring workspace is writable; the
    workspace itself and the gate's own result dir are."""
    ws, out = tmp_path / "score" / "counter8", tmp_path / "gate-out"
    ws.mkdir(parents=True)
    out.mkdir()
    (ws / "rtl").mkdir()
    (ws / "rtl" / "mine.v").write_text("module mine; endmodule\n")
    assert ladder.sandbox_preflight(ws, tmp_path / "score")["masked"]
    outside = tmp_path / "outside"
    outside.mkdir()
    p = _sandbox_run(ws, out, f"""
        cat {REF_RTL} > /dev/null 2>&1 && echo REF-READ
        cat rtl/mine.v > /dev/null && echo WS-READ
        echo x > {outside}/escape 2>/dev/null && echo OUTSIDE-WRITTEN
        echo x > {ladder.REPO}/engine/escape 2>/dev/null && echo REPO-WRITTEN
        echo x > {ws}/written && echo WS-WRITTEN
        echo x > /score-out/r && echo OUT-WRITTEN
        find {ladder.REPO}/corpus {ladder.REPO}/tests -mindepth 1 | grep -q . && echo MASK-LEAK
        test -d {ladder.REPO}/engine && echo ENGINE-SEEN
        exit 0""")
    words = set(p.stdout.split())
    assert p.returncode == 0, p.stderr
    assert {"WS-READ", "WS-WRITTEN", "OUT-WRITTEN", "ENGINE-SEEN"} <= words
    assert not words & {"REF-READ", "OUTSIDE-WRITTEN", "REPO-WRITTEN", "MASK-LEAK"}
    assert not (outside / "escape").exists()
    assert not (ladder.REPO / "engine" / "escape").exists()
    assert (ws / "written").is_file() and (out / "r").is_file()


ESCAPE_TB = """

import os as _os
import cocotb as _cocotb


@_cocotb.test()
async def test_escape(dut):
    tried = []
    for target in (TARGETS):
        try:
            with open(target, "w") as fh:
                fh.write("escaped")
            tried.append("WROTE " + target)
        except OSError as exc:
            tried.append(f"BLOCKED {target}: {exc.strerror}")
    with open(_os.path.join(WS, "tb-escape.log"), "w") as fh:
        fh.write("\\n".join(tried))
"""


def _scoring_ws(tmp_path: Path, arm: Path) -> tuple[Path, Path]:
    ws, outdir = tmp_path / "score" / "counter8", tmp_path / "score" / "gate-results"
    ladder.build_scoring_ws(ws, CORPUS8, arm, "counter8")
    outdir.mkdir()
    ladder.sandbox_preflight(ws, outdir)
    return ws, outdir


@pytest.mark.slow
@needs_bwrap
def test_rtl_including_the_reference_by_absolute_path_sees_nothing(tmp_path):
    """Real tools in the scoring sandbox: an arm whose rtl/ only includes the
    corpus reference by absolute path fails lint and synth (the include is
    missing); the reference RTL itself, copied in, passes both."""
    cheat = tmp_path / "cheat"
    (cheat / "rtl").mkdir(parents=True)
    (cheat / "rtl" / "counter8.v").write_text(f'`include "{REF_RTL}"\n')
    ws, outdir = _scoring_ws(tmp_path / "c", cheat)
    for g in ("lint", "synth"):
        r = ladder.run_gate(ws, g, outdir)
        assert r["status"] in ("fail", "error"), (g, r)
    honest = _arm(tmp_path / "honest", tb=False)
    ws, outdir = _scoring_ws(tmp_path / "h", honest)
    for g in ("lint", "synth"):
        r = ladder.run_gate(ws, g, outdir)
        assert r["status"] == "pass", (g, r)


@pytest.mark.slow
@needs_bwrap
def test_an_arm_testbench_cannot_write_outside_the_scoring_ws(tmp_path):
    """The arm's cocotb tb runs in the sim gate's sandbox: its writes to a
    host path outside the scoring workspace and into the repo fail and land
    nowhere; its write into the workspace works, and sim still passes."""
    arm = _arm(tmp_path / "arm")
    outside = tmp_path / "outside"
    outside.mkdir()
    targets = [str(outside / "escape"), str(ladder.REPO / "engine" / "escape"),
               str(Path.home() / "chip-flow-escape-probe")]
    tb = arm / "tb" / "test_counter8.py"
    ws_path = tmp_path / "s" / "score" / "counter8"
    tb.write_text(tb.read_text() + ESCAPE_TB.replace("TARGETS", repr(tuple(targets)))
                  .replace("WS", repr(str(ws_path))))
    ws, outdir = _scoring_ws(tmp_path / "s", arm)
    assert ws == ws_path
    r = ladder.run_gate(ws, "sim", outdir)
    assert r["status"] == "pass", r
    log = (ws / "tb-escape.log").read_text()
    for t in targets:
        assert f"BLOCKED {t}" in log, log
        assert not Path(t).exists()
