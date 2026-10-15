#!/usr/bin/env bash
# check-harden-faults.sh [RUNG] - the corpus rungs whose planted faults need
# full LibreLane hardens (tests/test_corpus_faults.py's HARDEN_RUNGS,
# marked `harden_faults`), run end to end through faults.py's run_rung.
# Split out of tests/check-slow.sh so their hardens (several per rung)
# never push that lane past its 1800 s cap. RUNG, when given, runs only
# that rung (a pytest -k match on its id, e.g. dac_tile); CI runs one job
# per rung. The cap catches a hang: not yet measured on CI (2026-10-15).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

eda_bin() {
  if [ -x "$REPO_ROOT/bin/eda" ]; then
    echo "$REPO_ROOT/bin/eda"
  elif [ -n "${EDA:-}" ] && [ -x "$EDA" ]; then
    echo "$EDA"
  else
    echo "check-harden-faults.sh: no ./bin/eda and \$EDA is not set to an " \
         "executable launcher" >&2
    exit 2
  fi
}

EDA_BIN="$(eda_bin)"
cd "$REPO_ROOT"
args=(tests/test_corpus_faults.py -q -m harden_faults)
if [ -n "${1:-}" ]; then args+=(-k "$1"); fi
exec timeout 3000 "$EDA_BIN" python -m pytest "${args[@]}"
