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
# CPU is read from /proc every CHECK_ENGINE_POLL_S (default 2) seconds and
# settled at the end from this shell's own cutime. A process that leaves
# the tree (reparented to init) stops counting; the suite does not do that.
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
TCK="$(getconf CLK_TCK)"
CPU_TICKS=$((CPU_S * TCK))

# tree_ticks PID: add to TREE_TICKS the CPU ticks PID and its live
# descendants have used, each including what it has reaped (cutime and
# cstime), so a finished child is counted once, in its parent. No subshell
# per poll: this runs every few seconds for the whole suite.
tree_ticks() {
  local line f kid
  local -a fields kids
  { read -r line < "/proc/$1/stat"; } 2>/dev/null || return 0
  read -r -a fields <<<"${line##*) }"
  # fields[0] is stat field 3 (state); utime, stime, cutime, cstime are
  # stat fields 14-17.
  TREE_TICKS=$((TREE_TICKS + fields[11] + fields[12] + fields[13] + fields[14]))
  for f in /proc/"$1"/task/*/children; do
    # The file ends without a newline, so `read` returns 1 even when it
    # filled kids; an empty array is the only sign of nothing read.
    kids=()
    { read -r -d '' -a kids < "$f"; } 2>/dev/null || true
    for kid in "${kids[@]}"; do tree_ticks "$kid"; done
  done
}

# reaped_ticks: set REAPED_TICKS to the CPU ticks of everything this shell
# has reaped (its own cutime + cstime), i.e. the finished suite and all it
# waited for. Read in this shell, not a subshell: a fork starts at zero.
reaped_ticks() {
  local line
  local -a fields
  read -r line < "/proc/$$/stat"
  read -r -a fields <<<"${line##*) }"
  REAPED_TICKS=$((fields[13] + fields[14]))
}

secs() { awk -v t="$1" -v k="$TCK" 'BEGIN { printf "%.1f", t / k }'; }

# The suite gets its own process group, so ending it ends its whole tree.
set -m
"$EDA_BIN" python -m pytest tests/ -q -m "not slow" &
SUITE=$!
set +m
trap 'kill -KILL -- "-$SUITE" 2>/dev/null || true' EXIT

end_suite() {
  kill -TERM -- "-$SUITE" 2>/dev/null || true
  sleep 2
  kill -KILL -- "-$SUITE" 2>/dev/null || true
  wait "$SUITE" 2>/dev/null || true
}

verdict=""
rc=0
start=$SECONDS
while kill -0 "$SUITE" 2>/dev/null; do
  TREE_TICKS=0
  tree_ticks "$SUITE"
  if [ "$TREE_TICKS" -gt "$CPU_TICKS" ]; then
    verdict=slow; break
  fi
  if [ $((SECONDS - start)) -ge "$WALL_S" ]; then
    verdict=hang; break
  fi
  sleep "$POLL_S"
done
wall=$((SECONDS - start))

if [ -n "$verdict" ]; then
  used="$TREE_TICKS"
  end_suite
else
  wait "$SUITE" || rc=$?
  reaped_ticks
  used="$REAPED_TICKS"
  # It can cross the budget between the last poll and its exit.
  if [ "$used" -gt "$CPU_TICKS" ]; then verdict=slow; fi
fi
trap - EXIT

case "$verdict" in
  slow)
    echo "check-engine.sh: FAIL slow - the suite used $(secs "$used") s of" \
         "CPU, over its ${CPU_S} s budget (${wall} s wall). That is the" \
         "suite's own work, not the box's load." >&2
    exit 125 ;;
  hang)
    echo "check-engine.sh: FAIL hang - the suite was still running at the" \
         "${WALL_S} s wall-clock backstop, having used only $(secs "$used") s" \
         "of its ${CPU_S} s CPU budget: it is waiting, not working." >&2
    exit 124 ;;
esac
echo "check-engine.sh: suite used $(secs "$used") s CPU of its ${CPU_S} s" \
     "budget in ${wall} s wall (pytest exit $rc)" >&2
exit "$rc"
