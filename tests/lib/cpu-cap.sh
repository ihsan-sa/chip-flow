# shellcheck shell=bash
# tests/lib/cpu-cap.sh - sourced, not run. Holds a command to a CPU budget
# with a wall-clock backstop, instead of a fixed wall-clock timeout.
#
# Wall time on this box measures the box's load, not the command: at load
# 113 on 6 cores the engine suite passed in 5m43 wall on ~33 s of CPU, and
# a fixed `timeout 120` killed it at 41%. So a budget here is CPU seconds
# used by the command's own process tree, and wall time only catches a
# command that is waiting rather than working.
#
#   cpu_cap CPU_S WALL_S CMD [ARGS...]
#
# runs CMD in its own process group (redirect around the call as usual)
# and returns:
#   * 125 if its tree used more than CPU_S of CPU - a real slowdown;
#   * 124 if it was still running at WALL_S of wall - a hang;
#   * otherwise CMD's own exit code.
# On 125 or 124 the whole group is ended. Either way it sets CPU_CAP_VERDICT
# (slow, hang or empty), CPU_CAP_USED (CPU seconds, one decimal),
# CPU_CAP_WALL (whole wall seconds) and CPU_CAP_NOTE (one line saying which
# of the three happened and the numbers). CPU is read from /proc every
# CPU_CAP_POLL_S (default 2) seconds and settled at the end from this
# shell's own cutime. A process that leaves the tree (reparented to init)
# stops counting. Call it in the shell that sources this or in a subshell;
# not in a pipeline stage whose shell reaps other children meanwhile.

# _cpu_cap_tree PID: add to _CPU_CAP_TICKS the CPU ticks PID and its live
# descendants have used, each including what it has reaped (cutime and
# cstime), so a finished child is counted once, in its parent. No subshell
# per poll: this runs every few seconds for the whole command.
_cpu_cap_tree() {
  local line f kid
  local -a fields kids
  { read -r line < "/proc/$1/stat"; } 2>/dev/null || return 0
  read -r -a fields <<<"${line##*) }"
  # fields[0] is stat field 3 (state); utime, stime, cutime, cstime are
  # stat fields 14-17.
  _CPU_CAP_TICKS=$((_CPU_CAP_TICKS + fields[11] + fields[12] + fields[13] + fields[14]))
  for f in /proc/"$1"/task/*/children; do
    # The file ends without a newline, so `read` returns 1 even when it
    # filled kids; an empty array is the only sign of nothing read.
    kids=()
    { read -r -d '' -a kids < "$f"; } 2>/dev/null || true
    for kid in "${kids[@]}"; do _cpu_cap_tree "$kid"; done
  done
}

# _cpu_cap_reaped: print the CPU ticks of everything this shell has reaped
# (its own cutime + cstime). $BASHPID, not $$: in a subshell $$ is still the
# parent. The caller takes a difference, so earlier children don't count.
_cpu_cap_reaped() {
  local line
  local -a fields
  read -r line < "/proc/$BASHPID/stat"
  read -r -a fields <<<"${line##*) }"
  _CPU_CAP_REAPED=$((fields[13] + fields[14]))
}

_cpu_cap_secs() {
  awk -v t="$1" -v k="$(getconf CLK_TCK)" 'BEGIN { printf "%.1f", t / k }'
}

cpu_cap() {
  local cpu_s="$1" wall_s="$2" poll_s="${CPU_CAP_POLL_S:-2}" v
  shift 2
  CPU_CAP_VERDICT="" CPU_CAP_USED="" CPU_CAP_WALL="" CPU_CAP_NOTE=""
  for v in "$cpu_s" "$wall_s" "$poll_s"; do
    case "$v" in
      ''|*[!0-9]*|0)
        CPU_CAP_NOTE="cpu_cap: the CPU budget, wall backstop and poll must be positive whole seconds (got '$v')"
        echo "$CPU_CAP_NOTE" >&2
        return 2 ;;
    esac
  done
  local cpu_ticks=$((cpu_s * $(getconf CLK_TCK)))
  local pid rc=0 used start old_trap old_cmd had_m=""
  _cpu_cap_reaped
  local before=$_CPU_CAP_REAPED

  # Its own process group, so ending it ends its whole tree. Restore the
  # caller's EXIT trap and job-control setting afterwards.
  old_trap="$(trap -p EXIT)"
  case $- in *m*) had_m=1 ;; esac
  set -m
  "$@" &
  pid=$!
  [ -n "$had_m" ] || set +m
  # `trap -p` prints `trap -- 'CMD' EXIT` with CMD quoted for reuse, so the
  # middle is safe to hand to eval: dying mid-run still runs the caller's.
  old_cmd="${old_trap#trap -- }"
  old_cmd="${old_cmd% EXIT}"
  # shellcheck disable=SC2064  # $pid and $old_cmd are meant to expand now
  trap "kill -KILL -- -$pid 2>/dev/null || true${old_trap:+; eval $old_cmd}" EXIT

  start=$SECONDS
  while kill -0 "$pid" 2>/dev/null; do
    _CPU_CAP_TICKS=0
    _cpu_cap_tree "$pid"
    if [ "$_CPU_CAP_TICKS" -gt "$cpu_ticks" ]; then
      CPU_CAP_VERDICT=slow; break
    fi
    if [ $((SECONDS - start)) -ge "$wall_s" ]; then
      CPU_CAP_VERDICT=hang; break
    fi
    sleep "$poll_s"
  done
  CPU_CAP_WALL=$((SECONDS - start))

  if [ -n "$CPU_CAP_VERDICT" ]; then
    used="$_CPU_CAP_TICKS"
    kill -TERM -- "-$pid" 2>/dev/null || true
    sleep 2
    kill -KILL -- "-$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  else
    wait "$pid" || rc=$?
    _cpu_cap_reaped
    used=$((_CPU_CAP_REAPED - before))
    # It can cross the budget between the last poll and its exit.
    if [ "$used" -gt "$cpu_ticks" ]; then CPU_CAP_VERDICT=slow; fi
  fi
  if [ -n "$old_trap" ]; then eval "$old_trap"; else trap - EXIT; fi
  CPU_CAP_USED="$(_cpu_cap_secs "$used")"

  case "$CPU_CAP_VERDICT" in
    slow)
      CPU_CAP_NOTE="FAIL slow - used ${CPU_CAP_USED} s of CPU, over its ${cpu_s} s budget (${CPU_CAP_WALL} s wall). That is its own work, not the box's load."
      return 125 ;;
    hang)
      CPU_CAP_NOTE="FAIL hang - still running at the ${wall_s} s wall-clock backstop, having used only ${CPU_CAP_USED} s of its ${cpu_s} s CPU budget: it is waiting, not working."
      return 124 ;;
  esac
  CPU_CAP_NOTE="used ${CPU_CAP_USED} s CPU of its ${cpu_s} s budget in ${CPU_CAP_WALL} s wall (exit $rc)"
  return "$rc"
}
