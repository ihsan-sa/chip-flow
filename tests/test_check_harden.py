"""engine/scripts/check_harden.py: the harden gate (docs/design.md 1.5's
harden row, "### M4."). A real LibreLane run is the better part of a
lifetime for a unit test (tests/smoke-harden.sh runs the real thing, by
hand); every case here fakes `subprocess.run` (the same pattern
tests/test_jobs.py uses for its own detached-process cases) to prove
check_harden.py's OWN failure classification - "the rule the last review
enforced three times: a gate that didn't run is a refusal, never a pass" -
without needing the toolchain image at all.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_harden  # noqa: E402


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "spec").mkdir(parents=True)
    (ws / "rtl" / "counter8.v").write_text(
        "module counter8(input wire clk, input wire rst, "
        "output reg [7:0] count);\n"
        "  always @(posedge clk) count <= rst ? 8'd0 : count + 8'd1;\n"
        "endmodule\n", encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\n"
        "requirements: []\n"
        "ports:\n"
        "  clk: {dir: input, width: 1}\n"
        "  rst: {dir: input, width: 1}\n"
        "  count: {dir: output, width: 8}\n"
        "clock: {period_ns: 20, domains: [clk]}\n"
        "tt_pins:\n"
        "  clk: clk\n"
        "  rst: ~rst_n\n"
        "  count: uo_out[7:0]\n", encoding="utf-8")
    return ws


def _fake_run_factory(librelane_behavior):
    """librelane_behavior(cwd: Path) -> CompletedProcess-like namespace for
    the `eda librelane ...` call; the `--print-toolchain-root` call always
    succeeds with a fake path."""
    def fake_run(cmd, cwd=None, **kwargs):
        if "--print-toolchain-root" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/toolchain\n", stderr="")
        assert cmd[1] == "librelane", cmd
        return librelane_behavior(Path(cwd))
    return fake_run


def test_launcher_never_reached_librelane_is_an_error(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)

    def behavior(cwd):
        # no flow.log anywhere - exactly a launcher that never reached
        # LibreLane's own flow object (a bad arg, eda misconfigured).
        return subprocess.CompletedProcess(["eda"], 127, stdout="", stderr="not found")

    monkeypatch.setattr(check_harden.subprocess, "run", _fake_run_factory(behavior))
    code = check_harden.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert out["status"] == "error"
    assert "never started" in out["error"]


def test_timeout_is_an_error_not_a_pass(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)

    def behavior(cwd):
        raise subprocess.TimeoutExpired(cmd=["eda", "librelane"], timeout=1.0)

    monkeypatch.setattr(check_harden.subprocess, "run", _fake_run_factory(behavior))
    code = check_harden.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert out["status"] == "error"
    assert "timed out" in out["error"]


def test_failing_step_is_a_violation_not_an_error(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)

    def behavior(cwd):
        run_dir = cwd / "runs" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "flow.log").write_text("... some steps ran ...\n", encoding="utf-8")
        (run_dir / "error.log").write_text(
            "ERROR: design too large for the tile\n", encoding="utf-8")
        return subprocess.CompletedProcess(["eda", "librelane"], 1, stdout="", stderr="")

    monkeypatch.setattr(check_harden.subprocess, "run", _fake_run_factory(behavior))
    code = check_harden.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["status"] == "violations"
    kinds = {v["kind"] for v in out["violations"]}
    assert "flow_step_failed" in kinds
    assert "too large for the tile" in out["violations"][0]["msg"]


def test_exit_zero_with_missing_final_artifacts_is_a_violation(tmp_path, monkeypatch, capsys):
    # "a launcher/flow that silently drops output" - exit 0 proves nothing
    # on its own; check_harden.py must still look for the artifacts.
    ws = make_ws(tmp_path)

    def behavior(cwd):
        run_dir = cwd / "runs" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "flow.log").write_text("Flow complete.\n", encoding="utf-8")
        # final/ deliberately left empty/missing
        return subprocess.CompletedProcess(["eda", "librelane"], 0, stdout="", stderr="")

    monkeypatch.setattr(check_harden.subprocess, "run", _fake_run_factory(behavior))
    code = check_harden.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["status"] == "violations"
    kinds = {v["kind"] for v in out["violations"]}
    assert "harden_missing_artifact" in kinds


def test_pass_when_flow_completes_and_final_artifacts_exist(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)

    def behavior(cwd):
        run_dir = cwd / "runs" / "run"
        final = run_dir / "final"
        for fmt in check_harden.EXPECTED_FORMATS:
            if fmt == "metrics.json":
                (final / "metrics.json").parent.mkdir(parents=True, exist_ok=True)
                (final / "metrics.json").write_text(
                    json.dumps({"design__instance__count": 42}), encoding="utf-8")
            else:
                d = final / fmt
                d.mkdir(parents=True, exist_ok=True)
                (d / f"tt_um_counter8.{fmt}").write_text("x", encoding="utf-8")
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "flow.log").write_text("Flow complete.\n", encoding="utf-8")
        return subprocess.CompletedProcess(["eda", "librelane"], 0, stdout="", stderr="")

    monkeypatch.setattr(check_harden.subprocess, "run", _fake_run_factory(behavior))
    code = check_harden.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["violations"] == []
    assert out["metrics"]["design__instance__count"] == 42


def test_no_tt_pins_refuses_before_touching_librelane(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\n"
        "ports: {clk: {dir: input, width: 1}}\n", encoding="utf-8")

    def behavior(cwd):
        raise AssertionError("librelane must never be invoked with no tt_pins")

    monkeypatch.setattr(check_harden.subprocess, "run", _fake_run_factory(behavior))
    code = check_harden.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "tt_pins" in out["error"]
