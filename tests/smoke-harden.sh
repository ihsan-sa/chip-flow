#!/usr/bin/env bash
# tests/smoke-harden.sh -- slow, not part of tests/check.sh: runs the real
# M4 pipeline (docs/design.md "### M4.") end to end on one corpus rung -
# harden (as a job, through jobs.py) then timing, drc, lvs, glsim and
# precheck, all against the real toolchain. Run once by hand and paste the
# result; give it minutes, several of them - LibreLane alone is 3-7 on
# these tiny designs, same as tests/smoke-librelane.sh's own note.
#
#   tests/smoke-harden.sh [rung]      # default: counter8
#
# The kill-halfway-then-restart case (docs/design.md 1.7) is exercised
# separately, in-process, by pytest (tests/test_jobs.py already covers
# jobs.py's own dead/restart mechanics generically; this script proves the
# harden gate's real wall time and that the six gates actually pass, which
# is what a unit test with a faked subprocess cannot).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
EDA="$REPO/bin/eda"
RUNG="${1:-counter8}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/chip-flow-smoke-harden.XXXXXX")"
echo "workspace: $WORK"

"$EDA" python "$REPO/engine/scripts/state.py" init --workspace "$WORK" \
  --skill vde --block "$RUNG" >/dev/null
cp "$REPO/corpus/vde/$RUNG/spec.md" "$WORK/spec/spec.md"
cp "$REPO/corpus/vde/$RUNG/spec.yaml" "$WORK/spec/spec.yaml"
cp "$REPO/corpus/vde/$RUNG"/rtl/*.v "$WORK/rtl/"
cp "$REPO/corpus/vde/$RUNG"/tb/*.py "$WORK/tb/" 2>/dev/null

PASS=0
FAIL=0
run_gate() {
  local gate="$1"
  echo "== $gate =="
  if "$EDA" python "$REPO/engine/scripts/check_$gate.py" \
      --workspace "$WORK" --out "$WORK/log/${gate}_result.json" < /dev/null; then
    echo "$gate: PASS"
    PASS=$((PASS + 1))
  else
    echo "$gate: FAIL - see $WORK/log/${gate}_result.json"
    FAIL=$((FAIL + 1))
  fi
}

echo "== harden (through jobs.py, as a real job) =="
START_JSON="$("$EDA" python "$REPO/engine/scripts/jobs.py" start --gate harden \
  --workspace "$WORK" --skill vde)"
echo "$START_JSON"
JOB_ID="$(echo "$START_JSON" | "$EDA" python3 -c 'import json,sys; print(json.load(sys.stdin)["job"])')"
STATUS=running
while [ "$STATUS" = running ]; do
  sleep 10
  STATUS_JSON="$("$EDA" python "$REPO/engine/scripts/jobs.py" status \
    --workspace "$WORK" --job "$JOB_ID")"
  STATUS="$(echo "$STATUS_JSON" | "$EDA" python3 -c "import json,sys; print(json.load(sys.stdin)['jobs']['$JOB_ID']['status'])")"
  echo "  job $JOB_ID: $STATUS"
done
if [ "$STATUS" = done ]; then
  echo "harden: PASS"; PASS=$((PASS + 1))
else
  echo "harden: FAIL ($STATUS) - see $WORK/log/jobs/job-$JOB_ID.log"; FAIL=$((FAIL + 1))
fi

for gate in timing drc lvs glsim precheck; do
  run_gate "$gate"
done

echo "== summary =="
printf 'smoke-harden.sh (%s): %d passed, %d failed. workspace left at %s\n' \
  "$RUNG" "$PASS" "$FAIL" "$WORK"
[ "$FAIL" -eq 0 ]
