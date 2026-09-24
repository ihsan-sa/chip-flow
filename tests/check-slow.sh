#!/usr/bin/env bash
# check-slow.sh - the engine's SLOW gate: every pytest test marked `slow`
# (tests/conftest.py) - the real-tool tests that need the eda image (sby,
# yosys, verilator, mcy, cocotb). Split out of tests/check-engine.sh because
# CLAUDE.md keeps tests/check.sh under two minutes and this set alone ran
# past that once M3's formal/cover/synth suites landed. Not wired into
# tests/check.sh - run this one by hand, or from a slower CI lane.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

eda_bin() {
  if [ -x "$REPO_ROOT/bin/eda" ]; then
    echo "$REPO_ROOT/bin/eda"
  elif [ -n "${EDA:-}" ] && [ -x "$EDA" ]; then
    echo "$EDA"
  else
    echo "check-slow.sh: no ./bin/eda and \$EDA is not set to an " \
         "executable launcher" >&2
    exit 2
  fi
}

EDA_BIN="$(eda_bin)"
cd "$REPO_ROOT"
# The cap catches a hang, not a slow box: M1-M3's own marked set (mutate is
# the slowest single gate there, ~20-25 s per run, several times over)
# measured ~146 s of wall time under load (uptime 20-30 on 6 cores). M4
# added one real end-to-end proof (test_check_harden.py's
# test_harden_job_killed_halfway_..., two full real LibreLane harden runs
# plus five real signoff gates) that alone measured ~815 s under load - the
# old 900 s cap left the rest of the slow set almost no room and one busy
# box away from a false-red timeout kill, not a real hang.
exec timeout 1800 "$EDA_BIN" python -m pytest tests/ -q -m slow
