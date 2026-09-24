#!/usr/bin/env bash
# check-engine.sh - the engine's own gate: run its pytest suite under two
# minutes. Landing wires this into tests/check.sh (docs/design.md, "###
# M1."); until then, run it directly.
#
# `-m "not slow"` excludes every real-tool test (needs the eda image - sby,
# yosys, verilator, mcy, cocotb; tests/conftest.py registers the `slow`
# marker) - those alone ran past two minutes once M3's formal/cover/synth
# suites landed (measured: ~146 s of the ~162 s full run). They still run,
# in tests/check-slow.sh, just not on this budget.
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
# The cap catches a hang, not a slow box: the not-slow subset passed in
# well under 20 s under load (uptime 20-30 on 6 cores).
exec timeout 120 "$EDA_BIN" python -m pytest tests/ -q -m "not slow"
