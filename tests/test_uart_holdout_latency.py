"""corpus/vde/uart's held-out and visible tests read a frame the way a UART
receiver does - wait for the start bit, sample each slot mid-bit - so they
accept any start latency spec.md allows. Regression for a holdout that
pinned the reference RTL's one-clock latency and failed a spec-conforming
zero-latency design one slot early. Runs REAL cocotb-on-Icarus."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "scripts"))
sys.path.insert(0, str(REPO / "engine" / "lib"))
sys.path.insert(0, str(REPO / "evals"))

import faults  # noqa: E402
import gate as gate_mod  # noqa: E402
import ladder  # noqa: E402

RUNG = REPO / "corpus" / "vde" / "uart"
ZERO_LATENCY = REPO / "tests" / "fixtures" / "uart_zero_latency" / "uart_tx.v"
PARITY = "^d  // PARITY"


def uart_ws(tmp_path: Path, rtl_text: str | None) -> Path:
    """A scratch copy of the uart rung; rtl/uart_tx.v replaced when given."""
    ws = faults.make_scratch_workspace(tmp_path, RUNG, "vde", "uart")
    if rtl_text is not None:
        (ws / "rtl" / "uart_tx.v").write_text(rtl_text, encoding="utf-8")
    shutil.rmtree(ws / "holdout", ignore_errors=True)
    return ws


def sim_status(ws: Path) -> str:
    row = gate_mod.load_gates(gate_mod.DEFAULT_GATES)["vde"]["sim"]
    report = gate_mod.run_report_for_gate(row, ws)
    return gate_mod.evaluate("sim", row, report)["status"]


def zero_latency_text() -> str:
    text = ZERO_LATENCY.read_text(encoding="utf-8")
    assert PARITY in text
    return text


@pytest.mark.slow
def test_reference_rtl_passes_holdout_and_sim(tmp_path):
    ws = uart_ws(tmp_path, None)
    held = ladder.run_holdout(ws, "vde", RUNG)
    assert held["status"] == "pass", held
    assert sim_status(ws) == "pass"


@pytest.mark.slow
def test_zero_latency_rtl_passes_holdout_and_sim(tmp_path):
    ws = uart_ws(tmp_path, zero_latency_text())
    held = ladder.run_holdout(ws, "vde", RUNG)
    assert held["status"] == "pass", held
    assert sim_status(ws) == "pass"


@pytest.mark.slow
def test_zero_latency_rtl_with_inverted_parity_fails_holdout(tmp_path):
    ws = uart_ws(tmp_path, zero_latency_text().replace(PARITY, "~^d"))
    held = ladder.run_holdout(ws, "vde", RUNG)
    assert held["status"] == "fail", held
    assert held["kinds"] == ["holdout_failed"]
