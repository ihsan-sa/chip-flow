#!/usr/bin/env bash
# tests/smoke-faults.sh -- slow, not part of tests/check.sh: runs faults.py
# over the WHOLE real corpus (every rung under corpus/vde/), which means the
# real mcy-driven mutate gate runs twice per rung (once clean, once on the
# "testbench that asserts nothing" fault) - minutes, not seconds, and
# slower again as more rungs join the corpus (docs/design.md "### M2.": "the
# PR reports mutate's wall time per rung"). Run once by hand and paste the
# JSON's `rungs` timing map.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
EDA="$REPO/bin/eda"

exec "$EDA" python "$REPO/engine/scripts/faults.py" --skill vde "$@"
