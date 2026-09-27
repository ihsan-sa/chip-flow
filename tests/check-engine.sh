#!/usr/bin/env bash
# check-engine.sh - the engine's own gate: run its pytest suite within two
# minutes of CPU (below). Landing wires this into tests/check.sh
# (docs/design.md, "### M1."); until then, run it directly.
#
# `-m "not slow"` excludes every real-tool test (needs the eda image - sby,
# yosys, verilator, mcy, cocotb; tests/conftest.py registers the `slow`
# marker) - those alone ran past two minutes once M3's formal/cover/synth
# suites landed (measured: ~146 s of the ~162 s full run). They still run,
# in tests/check-slow.sh, just not on this budget.
#
# THE BUDGET IS CPU TIME, NOT WALL TIME. "Under two minutes" is held as
# 120 s of CPU used by the suite's own process tree (pytest and everything
# it runs), because wall time on this box measures the box's load, not the
# suite: at load 113 on 6 cores the suite passed in 5m43 wall on ~33 s of
# CPU, and the old fixed `timeout 120` killed it at 41%. So:
#   * over CHECK_ENGINE_CPU_S (default 120) of CPU -> a real slowdown: the
#     suite is ended and this exits 125, saying "FAIL slow".
#   * past CHECK_ENGINE_WALL_S (default 1200) of wall -> a hang: the suite
#     is ended and this exits 124, saying "FAIL hang". The backstop is
#     generous on purpose; it only has to catch waiting, not working.
#   * otherwise pytest's own exit code, after one stderr line of CPU/wall.
# CPU is read from /proc every CHECK_ENGINE_POLL_S (default 2) seconds by
# tests/lib/cpu-cap.sh, which says how it counts.
# tests/test_check_engine.py plants a slow suite and a hung one.
#
# The launcher (bin/eda) is not on main yet (M0 lands it separately). This
# script reaches it through one helper that prefers ./bin/eda - once M0
# merges, this needs no change - and falls back to $EDA for the interim
# (set by whoever runs this against a worktree that has the launcher but
# hasn't merged it).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

eda_bin() {
  if [ -x "$REPO_ROOT/bin/eda" ]; then
    echo "$REPO_ROOT/bin/eda"
  elif [ -n "${EDA:-}" ] && [ -x "$EDA" ]; then
    echo "$EDA"
  else
    echo "check-engine.sh: no ./bin/eda and \$EDA is not set to an " \
         "executable launcher" >&2
    exit 2
  fi
}

EDA_BIN="$(eda_bin)"
cd "$REPO_ROOT"

CPU_S="${CHECK_ENGINE_CPU_S:-120}"
WALL_S="${CHECK_ENGINE_WALL_S:-1200}"
POLL_S="${CHECK_ENGINE_POLL_S:-2}"
for v in "$CPU_S" "$WALL_S" "$POLL_S"; do
  case "$v" in
    ''|*[!0-9]*|0)
      echo "check-engine.sh: CHECK_ENGINE_CPU_S, _WALL_S and _POLL_S must be" \
           "positive whole seconds (got '$v')" >&2
      exit 2 ;;
  esac
done
# The CPU/wall monitor is tests/lib/cpu-cap.sh, shared with tests/check.sh.
# shellcheck source=tests/lib/cpu-cap.sh
. "$REPO_ROOT/tests/lib/cpu-cap.sh"

rc=0
CPU_CAP_POLL_S="$POLL_S" cpu_cap "$CPU_S" "$WALL_S" \
  "$EDA_BIN" python -m pytest tests/ -q -m "not slow" || rc=$?

case "$CPU_CAP_VERDICT" in
  slow)
    echo "check-engine.sh: FAIL slow - the suite used $CPU_CAP_USED s of" \
         "CPU, over its ${CPU_S} s budget (${CPU_CAP_WALL} s wall). That is the" \
         "suite's own work, not the box's load." >&2
    exit 125 ;;
  hang)
    echo "check-engine.sh: FAIL hang - the suite was still running at the" \
         "${WALL_S} s wall-clock backstop, having used only $CPU_CAP_USED s" \
         "of its ${CPU_S} s CPU budget: it is waiting, not working." >&2
    exit 124 ;;
esac
echo "check-engine.sh: suite used $CPU_CAP_USED s CPU of its ${CPU_S} s" \
     "budget in ${CPU_CAP_WALL} s wall (pytest exit $rc)" >&2
exit "$rc"
