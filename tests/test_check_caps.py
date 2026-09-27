"""tests/check.sh holds its tool smokes to CPU budgets, not wall clocks.

check.sh runs twice, each in its own copy of the repo beside a stub bin/eda
that plays every tool. In the slow run the sby smoke is planted slow (a
CPU-bound grandchild) under a 1 s CPU budget and a wall backstop long
enough for a loaded box to hand it that second. In the hang run both netgen
smokes and `eda check-env` are planted hangs (sleeping, no CPU) under a 3 s
backstop and a CPU budget they cannot reach. The cocotb smoke passes in
both. A slow smoke's row must say "FAIL slow" and a hang's "FAIL hang",
never the other, the unplanted rows must not be flagged, and no planted
process may be left behind. tests/test_check_engine.py covers
the engine suite, which shares the same helper (tests/lib/cpu-cap.sh)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

STUB = r"""#!/usr/bin/env bash
case "$1" in
  --print-toolchain-root) echo "$STUB_DIR" ;;
  sby)
    [ "$STUB_MODE" = slow ] || exit 0
    bash -c 'while :; do :; done' &
    echo $! >> "$STUB_DIR/pids"; wait ;;
  netgen|check-env)
    [ "$STUB_MODE" = hang ] || exit 0
    sleep 1000 &
    echo $! >> "$STUB_DIR/pids"; wait ;;
  python3)
    [ "$2" = run_cocotb.py ] && echo "TESTS=1 PASS=1 FAIL=0"; exit 0 ;;
  *) exit 0 ;;
esac
"""


def run_check(tmp: Path, mode: str, cpu_s: int, wall_s: int):
    root = tmp / "repo"
    (root / "bin").mkdir(parents=True)
    shutil.copytree(REPO / "tests", root / "tests",
                    ignore=shutil.ignore_patterns("__pycache__", "fixtures"))
    # check.sh ends by running check-engine.sh: make that a quick pass
    # rather than this suite running itself.
    (root / "tests" / "check-engine.sh").write_text("exit 0\n")
    eda = root / "bin" / "eda"
    eda.write_text(STUB)
    eda.chmod(0o755)
    stub_dir = tmp / "stub"
    stub_dir.mkdir()
    env = dict(os.environ, STUB_DIR=str(stub_dir), TMPDIR=str(tmp),
               STUB_MODE=mode, CHECK_CPU_S=str(cpu_s),
               CHECK_WALL_S=str(wall_s), CPU_CAP_POLL_S="1")
    proc = subprocess.run(["bash", str(root / "tests" / "check.sh")],
                          env=env, capture_output=True, text=True,
                          timeout=300)
    rows = {}
    for line in proc.stdout.splitlines():
        if line.startswith("{"):
            row = json.loads(line)
            rows[row["tool"]] = row
    return proc, rows, stub_dir


@pytest.fixture(scope="module")
def slow_run(tmp_path_factory):
    return run_check(tmp_path_factory.mktemp("check-slow"), "slow", 1, 240)


@pytest.fixture(scope="module")
def hang_run(tmp_path_factory):
    return run_check(tmp_path_factory.mktemp("check-hang"), "hang", 600, 3)


def gone(stub_dir: Path, count: int) -> bool:
    """Every planted process is gone (a zombie awaiting init counts)."""
    pids = (stub_dir / "pids").read_text().split()
    assert len(pids) == count
    for pid in pids:
        try:
            stat = (Path("/proc") / pid / "stat").read_text()
        except FileNotFoundError:
            continue
        if not stat.rsplit(") ", 1)[1].startswith("Z"):
            return False
    return True


def test_planted_slow_smoke_fails_as_slow(slow_run):
    proc, rows, stub_dir = slow_run
    row = rows["sby-yices-bmc"]
    assert row["ok"] is False
    assert "FAIL slow" in row["detail"], proc.stderr
    assert "hang" not in row["detail"]
    assert "FAIL" not in rows["netgen-lvs-match"]["detail"]
    assert "FAIL" not in rows["python-imports"]["detail"]
    assert gone(stub_dir, 1)


def test_planted_hang_fails_as_hang_not_slow(hang_run):
    proc, rows, stub_dir = hang_run
    for tool in ("netgen-lvs-match", "netgen-lvs-mismatch"):
        assert rows[tool]["ok"] is False
        assert "FAIL hang" in rows[tool]["detail"], proc.stderr
        assert "slow" not in rows[tool]["detail"]
    assert "FAIL" not in rows["sby-yices-bmc"]["detail"]
    assert gone(stub_dir, 3)


def test_check_env_hang_fails_python_imports_as_hang(hang_run):
    _, rows, _ = hang_run
    assert rows["python-imports"]["ok"] is False
    assert "FAIL hang" in rows["python-imports"]["detail"]
    assert "FAIL hang" not in rows["cocotb-icarus"]["detail"]


def test_smoke_within_budget_still_passes(slow_run, hang_run):
    for proc, rows, _ in (slow_run, hang_run):
        assert rows["cocotb-icarus"]["ok"] is True, proc.stderr
        assert "cocotb: used" in proc.stderr
        assert proc.returncode == 1
