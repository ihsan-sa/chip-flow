"""engine/scripts/design_doc.py - the design document a block's release
builds, and the rule that only a released real design's document is filed
in the document register (cc-docs), never a draft, a test run or a corpus,
eval or ladder run.

The LaTeX build itself is pdf-material-builder's build.sh; these tests put a
fake one in its place (PDF_MATERIAL_BUILDER) that records the environment it
was given and, like the real one, calls `cc-docs` only when DOC_PROJECT is
set - with a fake `cc-docs` on PATH that logs every call."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "engine" / "lib"))

import pytest  # noqa: E402

import design_doc  # noqa: E402
import gate  # noqa: E402
import state as state_mod  # noqa: E402

from test_gate import make_checks_dir, make_gates_yaml  # noqa: E402

FAKE_BUILD = r'''#!/usr/bin/env bash
# a stand-in for pdf-material-builder's build.sh: log DOC_* and file the way
# the real one does, only when DOC_PROJECT is set
set -eu
tex="$1"; pdf="${tex%.tex}.pdf"
echo "DOC_PROJECT=${DOC_PROJECT:-} DOC_TITLE=${DOC_TITLE:-}" >> "$FAKE_LOG"
printf '%%PDF-1.4 fake\n' > "$pdf"
echo "built $pdf"
if [ -n "${DOC_PROJECT:-}" ] && command -v cc-docs >/dev/null; then
  filed=$(cc-docs file "$pdf" --project "$DOC_PROJECT" --title "$DOC_TITLE" --source "$tex")
  echo "register: $filed"
fi
'''

FAKE_CC_DOCS = r'''#!/usr/bin/env bash
echo "cc-docs $*" >> "$FAKE_CC_DOCS_LOG"
echo "004-0002-A      /filed/x.pdf"
'''


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """A fake build.sh and a fake cc-docs on PATH; returns the two logs."""
    builder = tmp_path / "pmb"
    (builder / "scripts").mkdir(parents=True)
    (builder / "scripts" / "build.sh").write_text(FAKE_BUILD, encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cc = bindir / "cc-docs"
    cc.write_text(FAKE_CC_DOCS, encoding="utf-8")
    cc.chmod(0o755)
    build_log, cc_log = tmp_path / "build.log", tmp_path / "cc-docs.log"
    monkeypatch.setenv("PDF_MATERIAL_BUILDER", str(builder))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(build_log))
    monkeypatch.setenv("FAKE_CC_DOCS_LOG", str(cc_log))
    monkeypatch.setattr(design_doc, "render_layout", lambda gds, png: "not rendered in tests")
    return build_log, cc_log


def make_ws(parent: Path, skill="vde", block="blk") -> Path:
    ws = parent / "blocks" / block
    state_mod.State.init(ws, skill, block)
    (ws / "spec" / "spec.md").write_text(
        "# blk\n\nA small block that `counts`.\n\n## Behaviour\n\nignored\n",
        encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(
        "top: blk\nrequirements:\n  - {id: REQ-A, text: it counts, check: sim}\n"
        "ports:\n  clk: {dir: input, width: 1}\n  q: {dir: output, width: 4}\n"
        "clock: {period_ns: 20, domains: [clk]}\n", encoding="utf-8")
    return ws


def record(ws: Path, gate_name: str, status: str, facts: dict, ts: str) -> None:
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    data.setdefault("gates", {})[gate_name] = {
        "status": status, "attempts": 1,
        "last": {"ts": ts, "status": status, "failing_count": 0, "total": 0,
                 "inputs": {"rtl": "dir_text:abc"}, "facts": facts}}
    (ws / "state.json").write_text(json.dumps(data), encoding="utf-8")


def released(monkeypatch, valid=True):
    monkeypatch.setattr(design_doc.attest_mod, "verify",
                        lambda ws: {"valid": valid, "reason": "no reports/checks.json",
                                    "attestation_sha256": "0" * 64})


# ------------------------------------------------------------------ filing

def test_test_run_never_calls_cc_docs(tmp_path, fakes, monkeypatch):
    """The brief: "a test showing a test/corpus run does not call cc-docs".
    A released workspace under pytest, asked with --file, builds a draft:
    build.sh gets no DOC_PROJECT and cc-docs is never run."""
    build_log, cc_log = fakes
    released(monkeypatch)
    ws = make_ws(tmp_path)
    payload, _, code = design_doc.run(["--workspace", str(ws), "--file"])
    assert code == 0 and payload["pdf"]
    assert payload["filed"] is False
    assert "test run" in payload["not_filed_because"]
    assert build_log.read_text() == "DOC_PROJECT= DOC_TITLE=\n"
    assert not cc_log.exists()


def test_corpus_run_never_calls_cc_docs(tmp_path, fakes, monkeypatch):
    """A released block inside a chip-flow checkout (the corpus, evals and
    ladder runs all live there) builds a draft, even outside pytest and the
    temp directory - while the same block outside a checkout files."""
    build_log, cc_log = fakes
    released(monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(design_doc.tempfile, "gettempdir", lambda: str(tmp_path / "elsewhere"))
    checkout = tmp_path / "chip-flow"
    (checkout / "engine" / "scripts").mkdir(parents=True)
    (checkout / "engine" / "scripts" / "task_router.py").write_text("")
    (checkout / "skills" / "vde").mkdir(parents=True)
    (checkout / "skills" / "vde" / "SKILL.md").write_text("")
    corpus_ws = make_ws(checkout / "runs" / "ladder")
    payload, _, _ = design_doc.run(["--workspace", str(corpus_ws), "--file"])
    assert payload["filed"] is False
    assert "corpus, eval or ladder run" in payload["not_filed_because"]
    assert not cc_log.exists()

    project_ws = make_ws(tmp_path / "project")
    payload, _, _ = design_doc.run(["--workspace", str(project_ws), "--file"])
    assert payload["not_filed_because"] is None and payload["filed"] is True
    assert payload["register"].startswith("004-0002-A")
    calls = cc_log.read_text().splitlines()
    assert len(calls) == 1 and "--project 004" in calls[0]
    assert "--title blk design document" in calls[0]
    assert f"--source {project_ws / 'doc' / 'blk_design.tex'}" in calls[0]


@pytest.fixture
def outside(tmp_path, monkeypatch):
    """A released block in a project outside any checkout, pytest's own
    marker and the temp directory taken away: filing_refusal() finds
    nothing, so each test below flips exactly one condition."""
    released(monkeypatch)
    monkeypatch.setattr(design_doc.tempfile, "gettempdir", lambda: str(tmp_path / "elsewhere"))
    ws = make_ws(tmp_path / "project")
    data = json.loads((ws / "state.json").read_text())
    assert design_doc.filing_refusal(ws, data, {}) is None
    return ws, data


def test_refuses_without_release(outside, monkeypatch):
    ws, data = outside
    released(monkeypatch, valid=False)
    assert design_doc.filing_refusal(ws, data, {}).startswith("not released")


def test_refuses_under_pytest_and_env_switch(outside):
    ws, data = outside
    assert "test run" in design_doc.filing_refusal(ws, data, {"PYTEST_CURRENT_TEST": "x"})
    assert "CHIPFLOW_DOC_NO_FILE" in design_doc.filing_refusal(ws, data, {"CHIPFLOW_DOC_NO_FILE": "1"})


def test_refuses_under_temp_dir(outside, monkeypatch, tmp_path):
    ws, data = outside
    monkeypatch.setattr(design_doc.tempfile, "gettempdir", lambda: str(tmp_path))
    assert "test run" in design_doc.filing_refusal(ws, data, {})


def test_refuses_a_nested_msde_side(outside):
    ws, data = outside
    side = ws / "digital"
    state_mod.State.init(side, "vde", "blk_digital")
    (ws / "state.json").write_text(json.dumps({**data, "skill": "msde"}))
    sdata = json.loads((side / "state.json").read_text())
    assert "nested side" in design_doc.filing_refusal(side, sdata, {})


def test_draft_strips_a_stray_doc_project(tmp_path, fakes, monkeypatch):
    """DOC_PROJECT left in the caller's environment must not file a draft:
    build.sh reads it straight from the environment."""
    build_log, cc_log = fakes
    monkeypatch.setenv("DOC_PROJECT", "004")
    monkeypatch.setenv("DOC_TITLE", "leaked")
    ws = make_ws(tmp_path)
    payload, _, _ = design_doc.run(["--workspace", str(ws)])
    assert payload["not_filed_because"] == "--file not given"
    assert build_log.read_text() == "DOC_PROJECT= DOC_TITLE=\n"
    assert not cc_log.exists()


# ---------------------------------------------------------------- content

def test_missing_gate_says_not_run(tmp_path):
    """"A missing gate result says so in the document rather than being
    left out": every gate the skill owes has a row, recorded or not."""
    ws = make_ws(tmp_path)
    record(ws, "sim", "pass", {"tests_passed": 6}, "2026-09-24T10:00:00")
    record(ws, "mutate", "fail", {"total_mutants": 19, "killed": 15, "kill_rate": 0.7895},
           "2026-09-24T10:05:00")
    payload, _, _ = design_doc.run(["--workspace", str(ws), "--no-build"])
    text = Path(payload["tex"]).read_text()
    owed = design_doc.attest_mod.applicable_gates("vde")
    assert set(payload["missing"]) == set(owed) - {"sim", "mutate"}
    for g in owed:
        assert r"\texttt{\small " + design_doc.tex(g) + "}" in text
    assert text.count("not run") >= len(payload["missing"])
    assert "What was not verified" in text
    assert "6 tests pass" in text and "kill rate 79.0" in text
    assert payload["failing"] == ["mutate"]
    assert "A draft: the block is not released" in text


def test_numbers_come_from_the_recorded_run_only(tmp_path):
    """A report on disk from another run is never read as the recorded one;
    gate.py's own recorded copy is, while its recorded_ts is the last ts."""
    ws = make_ws(tmp_path)
    ts = "2026-09-24T10:00:00"
    record(ws, "sim", "pass", {"tests_passed": 6}, ts)
    report = {"status": "pass", "input_digest": "dir_text:abc",
              "facts": {"tests_passed": 6, "tests_run": ["a"] * 9}}
    rpath = ws / "reports" / "gate-sim.json"
    rpath.parent.mkdir(exist_ok=True)
    rpath.write_text(json.dumps(report))
    data = json.loads((ws / "state.json").read_text())

    # written hours after the recorded run: not that run's
    r = design_doc.gate_record(ws, data, "sim")
    assert r["detail"] is None and "tests_run" not in r["facts"]

    # written as the recorded run finished: that run's
    t = datetime.fromisoformat(ts).timestamp() + 3
    os.utime(rpath, (t, t))
    r = design_doc.gate_record(ws, data, "sim")
    assert r["detail"] == "report" and len(r["facts"]["tests_run"]) == 9

    # the recorded copy wins, and only for its own ts
    rec = ws / "reports" / "recorded" / "gate-sim.json"
    rec.parent.mkdir()
    rec.write_text(json.dumps({**report, "facts": {"tests_passed": 6, "tests_run": ["a"] * 6},
                               "recorded_ts": ts}))
    r = design_doc.gate_record(ws, data, "sim")
    assert r["detail"] == "recorded" and len(r["facts"]["tests_run"]) == 6
    rec.write_text(json.dumps({**report, "recorded_ts": "2026-09-23T09:00:00"}))
    assert design_doc.gate_record(ws, data, "sim")["detail"] == "report"


def test_gate_writes_the_recorded_copy(tmp_path, capsys):
    """gate.py keeps the recorded run's whole result beside state.json,
    stamped with the ts it recorded, for design_doc.py to read."""
    ws = tmp_path / "ws"
    state_mod.State.init(ws, "vde", "counter8")
    code = gate.main(["--gate", "lint", "--workspace", str(ws),
                      "--gates", str(make_gates_yaml(tmp_path)),
                      "--checks-dir", str(make_checks_dir(tmp_path))])
    assert code == 0
    capsys.readouterr()
    data = json.loads((ws / "state.json").read_text())
    rec = json.loads((ws / "reports" / "recorded" / "gate-lint.json").read_text())
    assert rec["recorded_ts"] == data["gates"]["lint"]["last"]["ts"]
    assert rec["status"] == "pass" and rec["attempts"] == 1


def test_measures_use_the_scored_bounds_and_their_corners(tmp_path):
    """Analog measures read the bench's bounds sidecar (what sim_pvt scores)
    and only at the corners a bound names."""
    ws = make_ws(tmp_path, skill="ade")
    (ws / "tb").mkdir(exist_ok=True)
    (ws / "tb" / "b.bounds.json").write_text(json.dumps([
        {"measure": "f", "min": 1.0, "max": 2.0, "corners": ["tt"]},
        {"measure": "g", "max": 5.0, "corners": "all"}]))
    pvt = {"ran": True, "detail": "recorded", "facts": {"results": [
        {"corner": "tt", "measures": {"f": 1.5, "g": 1.0}},
        {"corner": "ss", "measures": {"f": 9.0, "g": 6.0}}]}}
    text = design_doc.measures_table(ws, {}, pvt)
    f_row = next(ln for ln in text.splitlines() if "{\\small f}" in ln)
    g_row = next(ln for ln in text.splitlines() if "{\\small g}" in ln)
    assert "1.5 &" in f_row and "out of bound" not in f_row
    assert "out of bound" in g_row
    kept_not = design_doc.measures_table(ws, {}, {**pvt, "detail": None})
    assert "not kept" in kept_not and "1.5" not in kept_not
