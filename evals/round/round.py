#!/usr/bin/env python
"""round.py - run one capped eval round: vde/counter8, bare against skill.

    eda python evals/round/round.py [--round-dir DIR] [--only ARM/DETAIL/REPEAT]
                                    [--dry-run]

The round is 18 headless Claude Code design runs: arms bare and skill x
detail levels terse, typical, full (corpus/vde/counter8/spec.terse.md,
spec.md, spec.full.md) x repeats 1-3. Cells run repeat-major (all six cells
of repeat 1, then 2, then 3; within a repeat terse, typical, full, and bare
before skill at each level) so drift spreads evenly. A cell that already
has <round>/run-results/<cell>.json is skipped, so running the same command
again resumes where the round stopped. One process, one run at a time. The
round dir defaults to ~/.cc/evals/chip-flow-counter8-round and must be
outside the repo.

HARD LIMITS, enforced here before and after every run:
  * total spend cap TOTAL_CAP_USD ($500), held in <round>/ledger.json. A run
    is never started unless spent + RUN_CAP_USD <= TOTAL_CAP_USD. "Spent"
    counts every finished run's recorded cost plus a reservation of the
    run's whole budget for any run that started and never recorded a cost
    (the runner died): the ledger can over-count, never under-count.
  * per-run cap RUN_CAP_USD ($70): claude gets --max-budget-usd
    min(70, 500 - spent); a stand-in resume gets what is left of it (none
    under MIN_RESUME_USD). A run whose recorded cost exceeds $70 stops the
    round. A run whose cost cannot be read from claude's JSON is booked at
    its whole budget. Claude's stdout counts only when the WHOLE of it
    parses as one JSON object: code the agent runs can write into claude's
    stdout (/proc/$PPID/fd/1), so a trailing line, or any second object, is
    "cost unknown", never a cost. Unknown cost also means no stand-in
    resume and no overrun check against a number the agent could set.
  * usage limit: before each run `cc-limit status`; a limit in force
    (exit 0) stops the round, it is never waited out. An answer that is
    neither "limit" (0) nor "clear" (1), or a cc-limit that does not run,
    stops it too: a check that did not run is a refusal.
  * load: while the 1-minute load average exceeds LOAD_MAX (12), sleep
    LOAD_SLEEP_S (60 s) and re-check (no model involved).
  * isolation: before EVERY run the probe (probe.py) runs in the exact
    sandbox argv the run will use; anything but a "pass" verdict refuses
    the run and stops the round. The round's first passing probe walks
    everything; later ones skip /usr and /etc files unchanged since it.
  * wall caps: INVOCATION_WALL_S (6 h) per claude invocation, PROBE_WALL_S
    (1 h) per probe, SCORE_WALL_S (4 h) per scoring; past it the process
    group is killed.
  * the real ~/.claude (bound read-write, see sandbox.py): its top-level
    entries are listed before and after EVERY claude invocation. Any entry
    new after one stops the round (state "stopped", the reason names the
    entries, nothing is deleted - a person decides), and the run's result
    records them (host_claude_dir_new). The credentials file must still be
    a regular, non-empty file owned by this user (stat only, never read)
    after every invocation, or the round stops the same way.
  * a run that raises (sandbox, export, git, I/O) stops the round.

Each run: a fresh run dir <round>/runs/<cell>/<stamp>/ (outside the repo)
with work/ (spec.md = the level's brief + evals/arms/footer.md, the same
footer in both arms, committed in a fresh git repo), home/ and
claude-projects/ (this run's session transcripts, kept so --resume works
across its invocations, seen by no other run). The probe, then a fresh
CONNECT proxy (proxy.py) and `claude -p PROMPT --output-format json --model
claude-opus-5-5 --permission-mode bypassPermissions --disallowedTools
WebFetch WebSearch --max-budget-usd X` in the sandbox (sandbox.py), cwd
/work. Skill arm: the repo's working tree, exported per invocation of this
script (sandbox.export_repo), read-only at ~/.claude/skills/chip-flow; when
claude returns with a human checkpoint presented (blocks/*/state.json
human.<H>.status == "presented"), the runner stands in as an approving
reviewer and resumes the same session with a reply quoting that
checkpoint's challenge, at most MAX_RESUMES (4) times; each stand-in
approval is recorded. Then the run is scored on the host by `evals/ladder.py
... --results-dir <round>/results --no-regen` (exit 2 = "not scored",
recorded, not fatal). One result JSON per run, <round>/run-results/<cell>.json,
holding the repo commit and dirty flag, and for the skill arm the export's
commit, dirty flag and tree hash.

At the end, or when the round stops early, it writes summary.json and
summary.md (cost and wall per run and per arm, counts per arm), runs
`scorecard.py --round <round>/results` (round-scorecard.json/.md land
there; never fatal), and sends exactly ONE cc-notify notice with the
terminal state. --dry-run prints the plan and the limit decisions and runs
the probe for each planned cell, but never calls claude, writes no ledger
entry or result, and sends no notice.

WHAT THE SANDBOX DOES NOT CLOSE (the probe cannot see these):
  * the OAuth credentials are readable inside the sandbox (claude needs
    them), so code the agent runs can call any path on the allowed hosts
    the token permits, outside claude's own accounting: another `claude -p`
    whose cost is not in total_cost_usd, or account endpoints beyond the
    Messages API. The proxy filters by host, not by path.
  * entries created in the real ~/.claude after a run's argv is built are
    visible unmasked in that run, and a new top-level entry created there
    from inside the sandbox lands in the host's real directory. The round
    detects it after the invocation and stops (see above) but does not
    prevent it, and an entry a host process adds during the same
    invocation is indistinguishable from one the agent added (it stops the
    round too).
  * the probe does not walk the EDA tree (bound read-only; too big) and
    does not read files over probe.MAX_BYTES (listed in its verdict).

Exit 0 finished (or dry run with every probe passing), 1 stopped early or a
probe refused, 2 error (with a `remediation`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import probe as probemod  # noqa: E402
import proxy as proxymod  # noqa: E402
import sandbox as sb  # noqa: E402

SKILL = "vde"
RUNG = "counter8"
MODEL = "claude-opus-5-5"
ARMS = ("bare", "skill")
DETAILS = ("terse", "typical", "full")
REPEATS = (1, 2, 3)
DETAIL_FILE = {"terse": "spec.terse.md", "typical": "spec.md",
               "full": "spec.full.md"}
PROMPTS = {"skill": "/vde full-run: design the block specified in spec.md",
           "bare": "Design the block specified in spec.md."}
APPROVAL = ("Approved, {challenge}. This is an unattended run: nobody else "
            "will answer, so continue to release.")

TOTAL_CAP_USD = 500.0
RUN_CAP_USD = 70.0
LOAD_MAX = 12.0
LOAD_SLEEP_S = 60.0
MAX_RESUMES = 4
MIN_RESUME_USD = 0.5
INVOCATION_WALL_S = 6 * 3600
PROBE_WALL_S = 3600
SCORE_WALL_S = 4 * 3600
TRUSTED_ROOTS = ("/usr", "/etc")
MAX_STATE_BYTES = 4 * 1024 * 1024   # a checkpoint state.json read cap
MAX_BLOCKS = 256                    # blocks/* entries looked at

RUNG_DIR = REPO / "corpus" / SKILL / RUNG
FOOTER = REPO / "evals" / "arms" / "footer.md"


class RoundError(RuntimeError):
    """The round cannot run at all. Exit 2."""


def default_round_dir() -> Path:
    return sb.real_home() / ".cc" / "evals" / f"chip-flow-{RUNG}-round"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cell_id(arm: str, detail: str, repeat: int) -> str:
    return f"{arm}-{detail}-r{repeat}"


def plan_cells(only: str | None = None) -> list[tuple[str, str, int]]:
    """The 18 cells, repeat-major, arms alternating within a detail level."""
    cells = [(a, d, r) for r in REPEATS for d in DETAILS for a in ARMS]
    if only:
        parts = only.split("/")
        if len(parts) != 3 or parts[0] not in ARMS or parts[1] not in DETAILS \
                or not parts[2].isdigit() or int(parts[2]) not in REPEATS:
            raise RoundError(f"--only wants ARM/DETAIL/REPEAT with ARM in "
                             f"{ARMS}, DETAIL in {DETAILS}, REPEAT in "
                             f"{REPEATS}; got {only!r}")
        cells = [(parts[0], parts[1], int(parts[2]))]
    return cells


def spec_text(detail: str) -> str:
    brief = RUNG_DIR / DETAIL_FILE[detail]
    for p in (brief, FOOTER):
        if not p.is_file():
            raise RoundError(f"{p} is missing")
    b = brief.read_text(encoding="utf-8").rstrip("\n")
    return b + "\n\n" + FOOTER.read_text(encoding="utf-8")


# ------------------------------------------------------------------ ledger
class Ledger:
    """<round>/ledger.json: one entry per run, "reserved" at its budget when
    it starts and "spent" at its recorded cost when it ends."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"total_cap_usd": TOTAL_CAP_USD,
                     "per_run_cap_usd": RUN_CAP_USD, "entries": []}
        if self.path.is_file():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def spent(self) -> float:
        return round(sum(float(e["usd"]) for e in self.data["entries"]), 6)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def reserve(self, run: str, usd: float) -> None:
        self.data["entries"].append({"run": run, "state": "reserved",
                                     "usd": usd, "at": now()})
        self._save()

    def settle(self, run: str, usd: float) -> None:
        for e in self.data["entries"]:
            if e["run"] == run and e["state"] == "reserved":
                e.update(state="spent", usd=usd, settled_at=now())
                break
        else:
            self.data["entries"].append({"run": run, "state": "spent",
                                         "usd": usd, "at": now()})
        self._save()


# --------------------------------------------------------- limit decisions
def budget_decision(spent: float) -> dict:
    """Start only if spent + RUN_CAP_USD <= TOTAL_CAP_USD."""
    room = TOTAL_CAP_USD - spent
    if spent + RUN_CAP_USD > TOTAL_CAP_USD + 1e-9:
        return {"ok": False, "spent": spent,
                "reason": f"budget: spent ${spent:.2f} + per-run cap "
                          f"${RUN_CAP_USD:.0f} > ${TOTAL_CAP_USD:.0f}"}
    return {"ok": True, "spent": spent,
            "run_budget": round(min(RUN_CAP_USD, room), 2)}


def usage_limit_decision(status_fn) -> dict:
    """status_fn() -> (exit code, text) of `cc-limit status`."""
    try:
        rc, text = status_fn()
    except (OSError, subprocess.SubprocessError) as exc:
        why = f"usage limit: cc-limit did not run ({exc}); refusing"
        return {"ok": False, "reason": why}
    text = (text or "").strip()
    if rc == 0:
        return {"ok": False, "reason": f"usage limit in force: {text}"}
    if rc == 1:
        return {"ok": True, "status": text or "clear"}
    why = f"usage limit: cc-limit status exited {rc} ({text}); refusing"
    return {"ok": False, "reason": why}


def wait_for_load(load_fn, sleep_fn, dry_run: bool = False,
                  log=lambda m: None) -> dict:
    """Block while the 1-minute load average exceeds LOAD_MAX."""
    waited = 0.0
    while True:
        load = load_fn()
        if load <= LOAD_MAX:
            return {"ok": True, "load": load, "waited_s": waited}
        if dry_run:
            return {"ok": True, "load": load, "waited_s": 0,
                    "note": f"load {load} > {LOAD_MAX}: a real run would "
                            f"sleep {LOAD_SLEEP_S:.0f} s and re-check"}
        log(f"load {load} > {LOAD_MAX}; sleeping {LOAD_SLEEP_S:.0f} s")
        sleep_fn(LOAD_SLEEP_S)
        waited += LOAD_SLEEP_S


def overrun_decision(cost: float) -> dict:
    if cost > RUN_CAP_USD:
        why = f"per-run overrun: run cost ${cost:.2f} > ${RUN_CAP_USD:.0f}"
        return {"ok": False, "reason": why}
    return {"ok": True}


def real_cc_limit_status() -> tuple[int, str]:
    r = subprocess.run(["cc-limit", "status"], capture_output=True, text=True,
                       timeout=60)
    return r.returncode, r.stdout + r.stderr


def real_loadavg() -> float:
    return float(Path("/proc/loadavg").read_text().split()[0])


# ------------------------------------------------------------ checkpoints
def _real_dir(p: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(p).st_mode)
    except OSError:
        return False


def read_regular(p: Path, cap: int = MAX_STATE_BYTES) -> str | None:
    """p's text when p is a regular file (lstat: never a link, FIFO, device
    or socket) of at most `cap` bytes, else None. Opened O_NOFOLLOW and
    O_NONBLOCK, re-checked with fstat, read at most cap+1 bytes."""
    try:
        if not stat.S_ISREG(os.lstat(p).st_mode):
            return None
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > cap:
            return None
        data = f.read(cap + 1)
    if len(data) > cap:
        return None
    return data.decode("utf-8", "replace")


def state_files(work: Path) -> list[Path]:
    """work/state.json and work/blocks/*/state.json, reached through real
    directories only (a linked blocks/ or blocks/<b> is skipped), at most
    MAX_BLOCKS blocks."""
    out = [work / "state.json"]
    blocks = work / "blocks"
    if _real_dir(work) and _real_dir(blocks):
        try:
            names = sorted(os.listdir(blocks))[:MAX_BLOCKS]
        except OSError:
            names = []
        out += [blocks / n / "state.json" for n in names
                if _real_dir(blocks / n)]
    return out


def open_checkpoints(work: Path) -> list[dict]:
    """Human checkpoints presented and not answered, from every
    blocks/*/state.json (and a state.json at the root) under work. Each is
    read only if it is a regular file under the size cap (read_regular);
    anything else - a link, a FIFO, a huge file, bad JSON - is skipped."""
    out = []
    for sj in sorted(state_files(work)):
        text = read_regular(sj)
        if text is None:
            continue
        try:
            st = json.loads(text)
        except (json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(st, dict) or not isinstance(st.get("human") or {},
                                                      dict):
            continue
        for cp, rec in sorted((st.get("human") or {}).items()):
            if isinstance(rec, dict) and rec.get("status") == "presented" \
                    and rec.get("challenge"):
                out.append({"state": str(sj.relative_to(work)),
                            "checkpoint": cp, "challenge": rec["challenge"]})
    return out


# ----------------------------------------------------------------- running
def claude_cmd(prompt: str, budget: float, resume: str | None = None) -> list:
    cmd = ["claude", "-p", prompt]
    if resume:
        cmd += ["--resume", resume]
    cmd += ["--output-format", "json", "--model", MODEL,
            "--permission-mode", "bypassPermissions",
            "--disallowedTools", "WebFetch", "WebSearch",
            "--max-budget-usd", f"{budget:.2f}"]
    return cmd


def run_group(argv: list[str], stdin_text: str | None, stdout_path: Path,
              stderr_path: Path, wall_s: float) -> dict:
    """Run argv in its own process group; kill the group past wall_s."""
    t0 = time.monotonic()
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin_text is not None
                             else subprocess.DEVNULL, stdout=out, stderr=err,
                             start_new_session=True)
        try:
            p.communicate(stdin_text.encode() if stdin_text is not None else None,
                          timeout=wall_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()
    return {"rc": p.returncode, "timed_out": timed_out,
            "wall_s": round(time.monotonic() - t0, 1)}


def parse_claude_json(path: Path) -> dict | None:
    """The WHOLE of `path` (whitespace-trimmed) parsed as one JSON object,
    else None. Never a last line or a trailing object: code inside the
    sandbox can append to claude's stdout, so anything but exactly one
    object is unreadable, and the caller books the run's whole budget."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        d = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return None
    return d if isinstance(d, dict) else None


def known_cost(c) -> float | None:
    """A cost claude reported, if it is a finite number >= 0; else None."""
    if isinstance(c, bool) or not isinstance(c, (int, float)):
        return None
    c = float(c)
    return c if math.isfinite(c) and c >= 0 else None


def claude_dir_entries() -> set[str]:
    return set(os.listdir(sb.real_home() / ".claude"))


def credentials_state() -> dict:
    """The host credentials file by lstat only (never read): ok when it is
    a regular, non-empty file owned by this user."""
    p = sb.real_home() / ".claude" / sb.CREDENTIALS
    try:
        st = os.lstat(p)
    except OSError as exc:
        return {"ok": False, "detail": str(exc)}
    reg = stat.S_ISREG(st.st_mode)
    return {"ok": reg and st.st_uid == os.getuid() and st.st_size > 0,
            "regular": reg, "uid": st.st_uid, "nonempty": st.st_size > 0,
            "mode": oct(st.st_mode & 0o777)}


def host_claude_dir_stop(before: set[str], after: set[str],
                         cred: dict) -> str | None:
    """Why the round must stop after an invocation, or None: a top-level
    entry of the real ~/.claude that is new since before the invocation,
    or a credentials file that is no longer sound."""
    new = sorted(after - before)
    if new:
        return ("new entries in the host's real ~/.claude after a claude "
                f"invocation: {', '.join(new)} (left in place; a person "
                "decides)")
    if not cred.get("ok"):
        return f"host credentials file changed: {cred}"
    return None


def repo_state() -> dict:
    """The repo's HEAD and whether its tracked tree differs from it."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                                check=True, capture_output=True,
                                text=True).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=REPO, check=True, capture_output=True, text=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"commit": None, "dirty": None, "error": str(exc)}
    return {"commit": commit, "dirty": dirty}


CELL_ERRORS = (sb.SandboxError, RoundError, OSError, ValueError, KeyError,
               subprocess.SubprocessError, json.JSONDecodeError)


class Round:
    """One round. Every outside effect is a method or an injected function,
    so the limit decisions are testable with fakes."""

    def __init__(self, round_dir: Path, dry_run: bool = False,
                 status_fn=real_cc_limit_status, load_fn=real_loadavg,
                 sleep_fn=time.sleep, notify_fn=None, log=None,
                 probe_roots: list[str] | None = None, proxy_connect=None):
        self.dir = Path(round_dir)
        self.dry_run = dry_run
        # What the probe walks: "/" (everything visible) unless a test
        # narrows it. The real round never passes probe_roots.
        self.probe_roots = list(probe_roots) if probe_roots else ["/"]
        self.proxy_connect = proxy_connect
        self.export_info: dict | None = None
        self.status_fn, self.load_fn, self.sleep_fn = status_fn, load_fn, sleep_fn
        self.notify_fn = notify_fn or self._notify
        self.log = log or (lambda m: print(f"[round {now()}] {m}",
                                           file=sys.stderr, flush=True))
        self.ledger = Ledger(self.dir / "ledger.json")
        self.results_dir = self.dir / "run-results"
        self.events: list[dict] = []

    # -- setup ------------------------------------------------------------
    def eda_bin_dir(self) -> Path:
        d = self.dir / "eda-bin"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / "bin" / "eda", d / "eda")
        return d

    def export(self) -> Path:
        """The skill arm's read-only export of the working tree, built fresh
        for this invocation and kept as <round>/export-<tree sha256[:12]>
        (an identical earlier export is reused). Its commit, dirty flag and
        tree hash go into every skill-arm run record."""
        tmp = self.dir / f"export-tmp-{os.getpid()}"
        if tmp.exists():
            shutil.rmtree(tmp)
        info = sb.export_repo(REPO, tmp)
        dest = self.dir / f"export-{info['tree_sha256'][:12]}"
        if dest.is_dir():
            shutil.rmtree(tmp)
        else:
            os.rename(tmp, dest)
        info["dir"] = str(dest)
        (self.dir / f"{dest.name}.json").write_text(
            json.dumps(info, indent=1), encoding="utf-8")
        self.export_info = info
        return dest

    def layout(self, arm: str, run_dir: Path, export: Path | None) -> sb.Layout:
        return sb.Layout(arm=arm, run_dir=run_dir,
                         claude_bin=sb.resolve_claude_bin(),
                         eda_tree=sb.resolve_eda_tree(),
                         eda_bin_dir=self.eda_bin_dir(),
                         real_claude_dir=sb.real_home() / ".claude",
                         export_dir=export if arm == "skill" else None)

    # -- probe ------------------------------------------------------------
    def baseline(self) -> dict | None:
        p = self.dir / "probe-baseline.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def run_probe(self, layout: sb.Layout) -> dict:
        """Run probe.py in the run's own sandbox argv. Any outcome but a
        parsed "pass" verdict is a refusal."""
        base = self.baseline()
        cfg = {"arm": layout.arm,
               "needles": probemod.corpus_fingerprints(RUNG_DIR),
               "skip_roots": [str(layout.eda_tree)],
               "skip_files": [f"{sb.SB_HOME}/.claude/{sb.CREDENTIALS}"],
               "mention_ok": ([f"{sb.SB_HOME}/.claude/skills/chip-flow"]
                              if layout.arm == "skill" else []),
               "proxy": f"127.0.0.1:{sb.PROXY_PORT}",
               "deny": list(probemod.DENY_TARGETS),
               "allow": probemod.ALLOW_TARGET,
               "roots": self.probe_roots}
        if base and self.probe_roots == ["/"]:
            cfg.update(trusted_roots=list(TRUSTED_ROOTS),
                       trusted_before=base["at_epoch"])
        argv = sb.build_argv(layout, ["/usr/bin/python3",
                                      f"{sb.SB_OPT}/probe.py"])
        started = time.time()
        rd = layout.run_dir
        with proxymod.ConnectProxy(layout.proxy_sock, rd / "proxy-probe.log",
                                   connect=self.proxy_connect):
            res = run_group(argv, json.dumps(cfg), rd / "probe.stdout",
                            rd / "probe.stderr", PROBE_WALL_S)
        verdict = parse_claude_json(rd / "probe.stdout") or {}
        ok = res["rc"] == 0 and verdict.get("status") == "pass" \
            and not res["timed_out"]
        verdict = {**verdict, "probe_rc": res["rc"],
                   "probe_timed_out": res["timed_out"],
                   "probe_wall_s": res["wall_s"], "ok": ok,
                   "proxy": proxymod.summarize_log(rd / "proxy-probe.log")}
        if not verdict.get("status"):
            verdict["status"] = "error"
            verdict["detail"] = "the probe printed no verdict: refused"
        (rd / "probe.json").write_text(json.dumps(verdict, indent=1),
                                       encoding="utf-8")
        if ok and not base and self.probe_roots == ["/"]:
            (self.dir / "probe-baseline.json").write_text(json.dumps(
                {"at_epoch": started, "at": now(), "roots": TRUSTED_ROOTS,
                 "run": str(rd)}, indent=1), encoding="utf-8")
        return verdict

    # -- one run ----------------------------------------------------------
    def gates(self) -> dict:
        """The limit decisions taken before every run, in order."""
        b = budget_decision(self.ledger.spent())
        if not b["ok"]:
            return {"ok": False, "reason": b["reason"], "budget": b}
        u = usage_limit_decision(self.status_fn)
        if not u["ok"]:
            return {"ok": False, "reason": u["reason"], "budget": b,
                    "usage": u}
        ld = wait_for_load(self.load_fn, self.sleep_fn, self.dry_run, self.log)
        return {"ok": True, "budget": b, "usage": u, "load": ld}

    def invoke(self, layout: sb.Layout, cmd: list[str], n: int) -> dict:
        rd = layout.run_dir
        argv = sb.build_argv(layout, cmd)
        out, err = rd / f"claude-{n}.stdout.json", rd / f"claude-{n}.stderr"
        with proxymod.ConnectProxy(layout.proxy_sock, rd / "proxy.log",
                                   connect=self.proxy_connect):
            res = run_group(argv, None, out, err, INVOCATION_WALL_S)
        j = parse_claude_json(out)
        rec = {"n": n, "cmd": cmd, "stdout": out.name, "stderr": err.name,
               **res, "json": j is not None}
        if j:
            for k in ("session_id", "num_turns", "is_error", "subtype",
                      "total_cost_usd", "duration_ms"):
                rec[k] = j.get(k)
        return rec

    def invoke_checked(self, layout: sb.Layout, cmd: list[str], n: int) -> dict:
        """invoke(), with the real ~/.claude's top-level entries listed
        before and after and the credentials file stat-ed after. A new entry
        or an unsound credentials file sets rec["stop"]; nothing is
        deleted."""
        before = claude_dir_entries()
        rec = self.invoke(layout, cmd, n)
        after = claude_dir_entries()
        cred = credentials_state()
        rec["host_claude_dir_new"] = sorted(after - before)
        rec["credentials_ok"] = bool(cred.get("ok"))
        why = host_claude_dir_stop(before, after, cred)
        if why:
            rec["stop"] = why
        return rec

    def run_cell(self, arm: str, detail: str, repeat: int, budget: float,
                 export: Path | None) -> dict:
        cid = cell_id(arm, detail, repeat)
        stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        rd = self.dir / "runs" / cid / stamp
        sb.prepare_run(rd, spec_text(detail))
        layout = self.layout(arm, rd, export)
        result = {"cell": cid, "skill": SKILL, "rung": RUNG, "arm": arm,
                  "detail": detail, "repeat": repeat, "model": MODEL,
                  "run_dir": str(rd), "workdir": str(layout.work),
                  "budget_usd": budget, "started": now(),
                  "repo": repo_state()}
        if arm == "skill":
            result["export"] = {k: (self.export_info or {}).get(k) for k in
                                ("commit", "dirty", "tree_sha256", "files",
                                 "dir")}
        verdict = self.run_probe(layout)
        result["probe"] = {k: verdict.get(k) for k in
                           ("ok", "status", "findings", "probe_rc",
                            "probe_wall_s", "net", "proxy", "detail")}
        result["probe"]["walk"] = {k: (verdict.get("walk") or {}).get(k) for k
                                   in ("files", "bytes", "seconds",
                                       "trusted_unchanged", "unreadable")}
        if not verdict["ok"]:
            result["refused"] = "probe did not pass"
            return result
        if self.dry_run:
            result["dry_run"] = True
            return result

        before = claude_dir_entries()
        self.ledger.reserve(cid, budget)
        t0 = time.monotonic()
        invocations, approvals = [], []
        cost = 0.0
        cost_known = True
        stop = None
        rec = self.invoke_checked(layout, claude_cmd(PROMPTS[arm], budget), 0)
        invocations.append(rec)
        while True:
            c = known_cost(rec.get("total_cost_usd"))
            if c is not None:
                cost += c
            else:
                cost_known = False
            stop = rec.get("stop")
            if stop or arm != "skill" or len(approvals) >= MAX_RESUMES:
                break
            cps = open_checkpoints(layout.work)
            sid = rec.get("session_id") or next(
                (i.get("session_id") for i in reversed(invocations)
                 if i.get("session_id")), None)
            left = round(budget - cost, 2)
            if not cps or not sid:
                break
            if not cost_known or left < MIN_RESUME_USD:
                result["resume_skipped"] = (f"checkpoint {cps[0]['checkpoint']}"
                                            f" open but ${left:.2f} left or "
                                            "cost unknown")
                break
            cp = cps[0]
            reply = APPROVAL.format(challenge=cp["challenge"])
            approvals.append({**cp, "reply": reply, "at": now(),
                              "by": "round.py stand-in reviewer"})
            rec = self.invoke_checked(
                layout, claude_cmd(reply, left, resume=sid), len(invocations))
            invocations.append(rec)
        wall = round(time.monotonic() - t0, 1)
        if not cost_known:
            cost = max(cost, budget)
        cost = round(cost, 6)
        self.ledger.settle(cid, cost)
        after = claude_dir_entries()
        result.update(cost_usd=cost, cost_known=cost_known, wall_s=wall,
                      invocations=invocations, stand_in_approvals=approvals,
                      open_checkpoints_at_end=open_checkpoints(layout.work),
                      proxy=proxymod.summarize_log(rd / "proxy.log"),
                      host_claude_dir_new=sorted(after - before),
                      credentials=credentials_state(), ended=now())
        if stop:
            result["stop"] = stop
            result["score"] = {"scored": False,
                               "why": "not scored: the round stopped"}
            return result
        result["score"] = self.score(arm, detail, repeat, layout.work, cost,
                                     wall, rd)
        return result

    def score(self, arm, detail, repeat, work, cost, wall, rd) -> dict:
        res_dir = self.dir / "results"
        res_dir.mkdir(parents=True, exist_ok=True)
        before = {p.name for p in res_dir.rglob("*.json")}
        argv = [str(REPO / "bin" / "eda"), "python",
                str(REPO / "evals" / "ladder.py"), "--skill", SKILL,
                "--rung", RUNG, "--arm", arm, "--detail", detail,
                "--repeat", str(repeat), "--model", MODEL,
                "--deliverables", str(work), "--hand-edits", "0",
                "--session-cost-usd", f"{cost:.4f}", "--wall-s", f"{wall:.1f}",
                "--results-dir", str(res_dir), "--no-regen"]
        try:
            res = run_group(argv, None, rd / "score.stdout", rd / "score.stderr",
                            SCORE_WALL_S)
        except OSError as exc:
            return {"scored": False, "error": str(exc), "argv": argv}
        out = parse_claude_json(rd / "score.stdout")
        new = sorted(p.name for p in res_dir.rglob("*.json")
                     if p.name not in before)
        return {"scored": res["rc"] in (0, 1) and not res["timed_out"],
                "rc": res["rc"], "timed_out": res["timed_out"],
                "status": "not scored" if res["rc"] == 2 else
                          (out or {}).get("status"),
                "result_files": new, "stdout": "score.stdout",
                "argv": argv, "summary": {k: (out or {}).get(k) for k in
                                          ("counts", "error", "remediation",
                                           "result_file", "phase")}}

    # -- the round --------------------------------------------------------
    def run(self, only: str | None = None) -> dict:
        """Plan, gate and run the cells. Past argument checks, every way the
        round ends - finished, a limit, a refused probe, a run that raised -
        goes through summarize(), which sends the one notice."""
        cells = plan_cells(only)
        self.dir.mkdir(parents=True, exist_ok=True)
        state, reason = "finished", None
        plans: list[dict] = []
        try:
            state, reason = self._run_cells(cells, plans)
        except CELL_ERRORS as exc:
            state, reason = "stopped", f"{type(exc).__name__}: {exc}"
        if self.dry_run:
            ok = state == "finished"
            return {"script": "round.py", "status": "pass" if ok else "violations",
                    "state": "dry-run" if ok else f"dry-run stopped: {reason}",
                    "plans": plans, "spent_usd": self.ledger.spent()}
        summary = self.summarize(state, reason)
        return {"script": "round.py",
                "status": "pass" if state == "finished" else "violations",
                "state": state if not reason else f"{state}: {reason}",
                "plans": plans, "summary": str(self.dir / "summary.md"),
                "spent_usd": self.ledger.spent(), **summary}

    def _run_cells(self, cells, plans: list[dict]) -> tuple[str, str | None]:
        for d in {c[1] for c in cells}:
            spec_text(d)
        export = self.export() if any(c[0] == "skill" for c in cells) else None
        state, reason = "finished", None
        for arm, detail, repeat in cells:
            cid = cell_id(arm, detail, repeat)
            if (self.results_dir / f"{cid}.json").is_file():
                plans.append({"cell": cid, "skipped": "has a result"})
                continue
            g = self.gates()
            plan = {"cell": cid, "gates": g}
            plans.append(plan)
            self.log(f"{cid}: {json.dumps(g)}")
            if not g["ok"]:
                state, reason = "stopped", g["reason"]
                break
            try:
                result = self.run_cell(arm, detail, repeat,
                                       g["budget"]["run_budget"], export)
            except CELL_ERRORS as exc:
                state = "stopped"
                reason = f"{cid} could not run: {type(exc).__name__}: {exc}"
                break
            plan["probe"] = result["probe"]
            if result.get("refused"):
                state = "stopped"
                pr = result["probe"]
                reason = (f"probe refused {cid}: "
                          f"{pr.get('findings') or pr.get('detail')}")
                (self.dir / "refused").mkdir(exist_ok=True)
                (self.dir / "refused" / f"{cid}-{now()}.json").write_text(
                    json.dumps(result, indent=1), encoding="utf-8")
                break
            if self.dry_run:
                continue
            self.results_dir.mkdir(parents=True, exist_ok=True)
            (self.results_dir / f"{cid}.json").write_text(
                json.dumps(result, indent=1), encoding="utf-8")
            if result.get("stop"):
                state, reason = "stopped", f"{cid}: {result['stop']}"
                break
            o = overrun_decision(result["cost_usd"])
            if not o["ok"]:
                state, reason = "stopped", o["reason"]
                break
            if not result["credentials"]["ok"]:
                state = "stopped"
                reason = f"host credentials file changed: {result['credentials']}"
                break
        return state, reason

    # -- summary ----------------------------------------------------------
    def summarize(self, state: str, reason: str | None) -> dict:
        runs = []
        for p in sorted(self.results_dir.glob("*.json")):
            r = json.loads(p.read_text(encoding="utf-8"))
            keys = ("cell", "arm", "detail", "repeat", "cost_usd", "wall_s")
            runs.append({k: r.get(k) for k in keys} |
                        {"scored": (r.get("score") or {}).get("scored"),
                         "is_error": any(i.get("is_error") for i in
                                         r.get("invocations", [])),
                         "approvals": len(r.get("stand_in_approvals", []))})
        per_arm = {}
        for a in ARMS:
            rs = [r for r in runs if r["arm"] == a]
            per_arm[a] = {"runs": len(rs),
                          "scored": sum(1 for r in rs if r["scored"]),
                          "errors": sum(1 for r in rs if r["is_error"]),
                          "cost_usd": round(sum(r["cost_usd"] or 0 for r in rs), 2),
                          "wall_s": round(sum(r["wall_s"] or 0 for r in rs), 1)}
        sc = self.scorecard()
        summary = {"state": state, "reason": reason, "at": now(),
                   "spent_usd": self.ledger.spent(), "cap_usd": TOTAL_CAP_USD,
                   "runs": runs, "per_arm": per_arm, "scorecard": sc}
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=1),
                                               encoding="utf-8")
        lines = [f"# {SKILL}/{RUNG} eval round: {state}"
                 + (f" ({reason})" if reason else ""), "",
                 f"Spent ${summary['spent_usd']:.2f} of ${TOTAL_CAP_USD:.0f}.",
                 "", "| arm | runs | scored | errors | cost $ | wall s |",
                 "|---|---|---|---|---|---|"]
        for a, v in per_arm.items():
            lines.append(f"| {a} | {v['runs']} | {v['scored']} | {v['errors']}"
                         f" | {v['cost_usd']:.2f} | {v['wall_s']:.0f} |")
        lines += ["", "| run | cost $ | wall s | scored | stand-in approvals |",
                  "|---|---|---|---|---|"]
        for r in runs:
            lines.append(f"| {r['cell']} | {(r['cost_usd'] or 0):.2f} | "
                         f"{(r['wall_s'] or 0):.0f} | {r['scored']} | "
                         f"{r['approvals']} |")
        if sc.get("md"):
            lines += ["", f"Paired scorecard: {sc['md']}"]
        (self.dir / "summary.md").write_text("\n".join(lines) + "\n",
                                             encoding="utf-8")
        msg = (f"vde/{RUNG} eval round {state}" + (f": {reason}" if reason else "")
               + f". {len(runs)} of 18 runs have results, ${summary['spent_usd']:.2f}"
               f" spent. Summary: {self.dir / 'summary.md'}")
        summary["notice"] = self.notify_fn(state, msg)
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=1),
                                               encoding="utf-8")
        return {"per_arm": per_arm, "notice": summary["notice"]}

    def scorecard(self) -> dict:
        """scorecard.py --round when it has that option; never fatal."""
        sc = REPO / "evals" / "scorecard.py"
        eda = str(REPO / "bin" / "eda")
        res_dir = self.dir / "results"
        if not sc.is_file() or not res_dir.is_dir():
            return {"ran": False, "why": "no scorecard.py or no results"}
        try:
            h = subprocess.run([eda, "python", str(sc), "--help"],
                               capture_output=True, text=True, timeout=300)
            if "--round" not in h.stdout:
                return {"ran": False, "why": "scorecard.py has no --round"}
            out = res_dir / "round-scorecard.json"
            md = res_dir / "round-scorecard.md"
            r = subprocess.run([eda, "python", str(sc), "--round", str(res_dir)],
                               capture_output=True, text=True, timeout=1800)
            return {"ran": True, "rc": r.returncode,
                    "json": str(out) if out.is_file() else None,
                    "md": str(md) if md.is_file() else None,
                    "stdout_tail": r.stdout[-500:]}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ran": False, "why": str(exc)}

    def _notify(self, state: str, msg: str) -> dict:
        try:
            r = subprocess.run(["cc-notify", "-t", f"chip-flow eval round {state}",
                                msg], capture_output=True, text=True, timeout=120)
            return {"sent": r.returncode == 0, "rc": r.returncode}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"sent": False, "error": str(exc)}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--round-dir", default=None)
    ap.add_argument("--only", help="ARM/DETAIL/REPEAT, e.g. skill/typical/1")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rdir = Path(a.round_dir) if a.round_dir else default_round_dir()
    try:
        if rdir.resolve().is_relative_to(REPO.resolve()):
            raise RoundError(f"--round-dir {rdir} is inside the repo; runs "
                             "must live outside it")
        out = Round(rdir, dry_run=a.dry_run).run(a.only)
    except (RoundError, sb.SandboxError, ValueError, OSError,
            subprocess.CalledProcessError) as exc:
        print(json.dumps({"script": "round.py", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}",
                          "remediation": "fix what the error names (a missing "
                                         "brief, footer, toolchain or claude "
                                         "binary) and run again; finished "
                                         "cells are kept"}))
        return 2
    print(json.dumps(out, indent=1))
    return 0 if out["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
