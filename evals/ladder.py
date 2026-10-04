#!/usr/bin/env python
"""ladder.py - score one skill run against the corpus ladder, and regenerate
evals/ladder.md (docs/design.md section 3, "### M6.").

    ladder.py --skill vde --rung uart --run WS --hand-edits N [--rulings R]
              [--session-tokens T] [--session-cost-usd C] [--wall-s S]
              [--note TEXT] [--reference] [PROVENANCE]
    ladder.py --skill vde --rung counter8 --deliverables WS --hand-edits N
              [--session-tokens T] [--session-cost-usd C] [--wall-s S]
              [--note TEXT] [--keep-scoring-ws DIR] [PROVENANCE]
    ladder.py --regen

    PROVENANCE: [--arm bare|skill] [--detail terse|typical|full]
                [--repeat N] [--model ID]
    Either scoring mode also takes [--no-regen] [--results-dir DIR]
    [--ladder-md FILE] [--out FILE].

It scores a run a session already made; it never drives an agent.

`--run WS` scores a skill run from its own record. From the
run's workspace it records whether every gate went green (attest.py's own
check: every gate the skill owes has a fresh pass or a bound waiver, no
open issue, plus a fresh `release` pass), then runs the corpus's own
held-out tests, which the run never saw, on a copy of the workspace: the
strong form of docs/design.md section 2. Beside those it records the kill
rate, area and worst slack from the gates' recorded facts, the fix attempts
(gate attempts past the first), the tokens and cost from the spawn ledger
plus the orchestrating session's own, and the wall time.

`--deliverables WS` scores one arm of a bare-against-skill round
(docs/design-evals.md section 3) from what the arm left, never from its
state.json or its recorded gate passes, so the bare arm (which has neither)
and the skill arm go through the same code path. The deliverables are the
ones evals/arms/footer.md asks for: rtl/, tb/ and, for a hardened block, the
LibreLane run's harden/runs/run/final/, at WS's root or under
blocks/<name>/ (the root when it has rtl/, else blocks/<spec top or rung>,
else the only blocks/* with rtl/). A scoring workspace is built in a temp
dir: state.py init, the corpus rung's spec.yaml, spec.md, formal/ and
holdout/, then the arm's rtl/, tb/ and final/. The arm's own spec/,
formal/ and records are never copied, so its rulings are never read
(--rulings must be 0). Every vde gate then runs there through gate.py:
  function        holdout (corpus held-out tests) and formal (corpus
                  properties) on the arm's RTL, with spec_lint, lint, synth;
  verification    sim, mutate and cover on the arm's OWN tb/; with no
                  tb/test_*.py they are not run and read "no testbench";
  implementation  the arm's own hardened run, never re-hardened (that would
                  hand the bare arm credit it did not earn): harden is
                  check_harden.py's completeness rule on the copied final/,
                  recorded through gate.py --report; timing, drc, lvs,
                  glsim and precheck run on it. With no final/ they are not
                  run and read "not hardened";
  release         last, as in a skill run.
Deliverables are untrusted: only regular files are copied, every symlink
(file or directory, at any depth, the roots included), device and fifo is
skipped and listed in the result's deliverables.skipped, nothing copied
keeps an exec bit, and nothing from them runs outside the gates that
normally run it. Corpus-graded gates run before any gate that executes the
arm's testbench, every copied tree is hashed before and after each gate,
and a change stops scoring and the rung counting (`integrity`). Each gate's
result is read from a file outside the scoring workspace, in a directory
of its own that no other gate's sandbox sees.

The scoring workspace is built on the host; every gate then runs in a
bubblewrap sandbox of its own (`gate_sandbox_argv`), because the arm's RTL
is read by the tools (an `include of an absolute path would read whatever
the path names) and its tb/ is code that runs: --unshare-all (no network),
--die-with-parent, --new-session, --clearenv with an explicit env;
/usr, /etc and /bin, /lib* read-only; the EDA tree read-only; the repo
read-only at its own path with corpus/, evals/, tests/ and docs/ masked by
an empty tmpfs (engine/ and bin/ stay visible); the scoring workspace
read-write; the gate's own result directory read-write at /score-out;
tmpfs /tmp and HOME (plus, read-only, the host's precheck python-deps
cache when there is one: there is no network to install it). The sandbox
is mandatory: no bwrap, or a bwrap that cannot start, is exit 2 before any
gate runs, never an unsandboxed run. The result records it (`sandbox`). The
result is
`kind: "round"`, with `gates` (per-gate status: pass, fail, error, not
hardened, no testbench, not run), `hardened`, `testbench`, `deliverables`
and `scoring_wall_s`; it is listed in ladder.md's own "Bare against skill
rounds" section and never on a skill column or as a reference. vde only.

Every result records `arm` (default skill), `detail` (default typical),
`repeat`, `model`, `chip_flow_commit` (git HEAD), `tool_image` (the
EDA_TOOLCHAIN tree's name) and `scoring` ("state" for --run,
"deliverables").

Hand edits cannot be read off a workspace, so the caller declares them with
`--hand-edits`; it is required. A rung COUNTS when every gate is green, the
held-out tests pass, and there were no hand edits.

Rulings are not hand edits. A ruling is the owner's answer on one mutation
survivor in a workspace's spec/mutant_rulings.yaml (engine/lib/rulingslib.py),
a documented human channel like an H-gate answer, so it is scored as its own
field and does not stop a rung counting. The score counts the entries in
every spec/mutant_rulings.yaml under the run (a /msde run nests one per side)
and the caller declares with `--rulings` (default 0) how many of them the
owner wrote. Nothing stops the run under test writing that file itself - the
skills only say no agent may - so an entry beyond the declared count is
scored as an undeclared ruling and stops the rung like a hand edit;
declaring more than the file holds is an error.

`--reference` marks a run that is not a skill session (the corpus reference
RTL with its gates run by gate.py, say). Its result is kept, but ladder.md
lists it under "Reference baselines" and never on the skill's column, which
shows only skill runs.

Every scored run is a dated JSON under <results-dir>/ladder/, named
<stamp>_<skill>_<rung>.json, plus _<arm>_<detail>_r<repeat> for a
--deliverables result or one given --repeat. ladder.md is
rebuilt (unless --no-regen) from the newest result per rung, the bench
baselines under evals/fixtures/, and the newest full-subset CVDP result under
evals/results/cvdp/ (the pinned non-agentic file, no --category, no --limit,
every subset problem selected; the newest other run, labelled as such, when
there is none), so the ade and msde columns fill in as their results land.
Exit 0 the rung counts, 1 it does not (the findings say why), 2 error;
`--regen` exits 0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

EVALS = Path(__file__).resolve().parent
REPO = EVALS.parent
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))
import attest  # noqa: E402
import checklib  # noqa: E402
import cocotblib  # noqa: E402
import gate as gate_mod  # noqa: E402
import rulingslib  # noqa: E402
import statelib  # noqa: E402
import ttlib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "ladder"
CORPUS = REPO / "corpus"
RESULTS = EVALS / "results"
SKIP_COPY = {"runs", "state_snapshots", "log", "__pycache__"}
# evals/cvdp/run.py DATASETS["nonagentic"]["file"]: the benchmark the page reports
CVDP_PINNED_FILE = "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"


def load_ladder() -> dict:
    import yaml
    return yaml.safe_load((EVALS / "ladder.yaml").read_text(encoding="utf-8"))


def _ts(s: str | None):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def gates_green(ws: Path, data: dict) -> list[str]:
    """Why the run is not all-green; empty when it is."""
    _body, problems = attest.build(ws)
    fresh = statelib.freshness_report(data, ws).get("gates", {})
    rel = (data.get("gates") or {}).get("release") or {}
    if rel.get("status") != "pass":
        problems.append("release: no recorded pass")
    elif not (fresh.get("release") or {}).get("fresh"):
        problems.append("release: its pass is stale")
    return problems


def run_holdout(ws: Path, skill: str, rung_dir: Path) -> dict:
    """The corpus's held-out tests on a copy of the run's workspace."""
    rows = gate_mod.load_gates(gate_mod.DEFAULT_GATES).get(skill) or {}
    if "holdout" not in rows:
        return {"status": "n/a", "why": f"{skill} has no holdout gate"}
    src = rung_dir / "holdout"
    if not src.is_dir():
        raise CheckError(f"corpus rung {rung_dir.name} has no holdout/")
    with tempfile.TemporaryDirectory(prefix="chip-flow-ladder-") as tmp:
        copy = Path(tmp) / ws.name
        shutil.copytree(ws, copy, ignore=lambda d, names: [
            n for n in names if n in SKIP_COPY])
        if (copy / "holdout").exists():
            shutil.rmtree(copy / "holdout")
        shutil.copytree(src, copy / "holdout")
        try:
            report = gate_mod.run_report_for_gate(rows["holdout"], copy)
        except Exception as exc:  # noqa: BLE001  a gate that did not run
            raise CheckError(f"the held-out tests could not run: {exc}") from exc
        result = gate_mod.evaluate("holdout", rows["holdout"], report)
    return {"status": result["status"],
            "tests_passed": report.get("tests_passed"),
            "tests_run": len(report.get("tests_run") or []),
            "kinds": sorted({v.get("kind") for v in result.get("failing", [])
                             if v.get("kind")})}


def count_rulings(ws: Path) -> int:
    """Entries across every spec/mutant_rulings.yaml under the run."""
    import yaml
    n = 0
    for f in sorted(ws.rglob(rulingslib.RULINGS_REL)):
        if SKIP_COPY & set(f.relative_to(ws).parts):
            continue
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise CheckError(f"{f.relative_to(ws)} is not valid YAML: {exc}") from exc
        if data is None:
            continue
        if not isinstance(data, dict):
            raise CheckError(f"{f.relative_to(ws)} is not a YAML mapping")
        for key, entries in data.items():
            if key not in rulingslib.FIELDS or not isinstance(entries, (list, type(None))):
                raise CheckError(f"{f.relative_to(ws)}: unexpected {key!r} "
                                 "(see engine/lib/rulingslib.py)")
            n += len(entries or [])
    return n


def facts(data: dict, gate: str) -> dict:
    return ((data.get("gates") or {}).get(gate, {}).get("last") or {}).get("facts") or {}


def worst_slack(corners) -> float | None:
    vals = [c.get("setup_ws") for c in (corners or {}).values()
            if isinstance(c, dict) and isinstance(c.get("setup_ws"), (int, float))]
    return min(vals) if vals else None


def score(args) -> tuple[dict, list[dict]]:
    ws = Path(args.run).resolve()
    rung_dir = CORPUS / args.skill / args.rung
    if not rung_dir.is_dir():
        raise CheckError(f"no corpus rung at corpus/{args.skill}/{args.rung}")
    data = checklib.load_json(ws / "state.json", "run state.json")
    if data.get("skill") != args.skill:
        raise CheckError(f"the run is a {data.get('skill')!r} workspace, not {args.skill!r}")

    found = count_rulings(ws)
    if args.rulings < 0 or args.rulings > found:
        raise CheckError(f"--rulings {args.rulings} declared, but the run's "
                         f"{rulingslib.RULINGS_REL} files hold {found} entries")
    # brief: a ruling "does not stop the rung counting", but the run's own
    # writes to the rulings file are counted "as hand edits"
    undeclared = found - args.rulings
    green_problems = gates_green(ws, data)
    held = run_holdout(ws, args.skill, rung_dir)
    gates = data.get("gates") or {}
    spawns = data.get("spawns") or []
    tokens = sum(int(s.get("tokens") or 0) for s in spawns) + (args.session_tokens or 0)
    cost = sum(float(s.get("cost_usd") or 0) for s in spawns) + (args.session_cost_usd or 0)
    hist = [_ts(h.get("ts")) for h in data.get("history") or []]
    hist = [h for h in hist if h]
    wall = args.wall_s if args.wall_s is not None else (
        (max(hist) - min(hist)).total_seconds() if len(hist) > 1 else None)

    counts = (not green_problems and held["status"] in ("pass", "n/a")
              and args.hand_edits == 0 and undeclared == 0)
    result = {
        "skill": args.skill, "rung": args.rung, "block": data.get("block"),
        "run": ws.name, "phase": data.get("phase"),
        "scored_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
        "gates_green": not green_problems,
        "gate_problems": green_problems,
        "gates_passed": sorted(g for g, e in gates.items() if e.get("status") == "pass"),
        "hand_edits": args.hand_edits,
        "rulings": args.rulings,
        "rulings_undeclared": undeclared,
        "held_out": held,
        "kill_rate": facts(data, "mutate").get("kill_rate"),
        "line_pct": facts(data, "cover").get("line_pct"),
        "area": facts(data, "synth").get("area"),
        "worst_slack_ns": worst_slack(facts(data, "timing").get("corners")),
        "fix_attempts": sum(max(0, int(e.get("attempts") or 0) - 1)
                            for e in gates.values()),
        "tokens": tokens or None,
        "cost_usd": round(cost, 2) if cost else None,
        "wall_s": round(wall) if wall is not None else None,
        "note": args.note,
        "kind": "reference" if args.reference else "skill",
    }
    result.update(provenance(args, "state"))
    violations = [checklib.violation("ladder", "error", None, args.rung,
                                     "gate_not_green", [], p, "attest")
                  for p in green_problems]
    if held["status"] == "fail":
        violations.append(checklib.violation(
            "ladder", "error", "holdout", args.rung, "holdout_failed", [],
            f"the corpus held-out tests fail ({', '.join(held['kinds'])})", "holdout"))
    if args.hand_edits:
        violations.append(checklib.violation(
            "ladder", "error", None, args.rung, "hand_edits", [],
            f"{args.hand_edits} hand edit(s) declared", "caller"))
    if undeclared:
        violations.append(checklib.violation(
            "ladder", "error", None, args.rung, "rulings_undeclared", [],
            f"{undeclared} mutant ruling(s) in the run that the caller did not "
            "declare as the owner's (--rulings): scored as hand edits", "caller"))
    return result, violations


# ---- provenance ------------------------------------------------------------

def _git_head() -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (p.stdout.strip() or None) if p.returncode == 0 else None


def _tool_image() -> str | None:
    """The EDA_TOOLCHAIN tree's name, as bin/eda resolves it."""
    try:
        p = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        p = None
    root = (p.stdout.strip() if p is not None and p.returncode == 0 else "") \
        or os.environ.get("EDA_TOOLCHAIN") or ""
    return Path(root).name or None


def provenance(args, scoring: str) -> dict:
    return {"arm": args.arm, "detail": args.detail, "repeat": args.repeat,
            "model": args.model, "chip_flow_commit": _git_head(),
            "tool_image": _tool_image(), "scoring": scoring}


# ---- --deliverables: score what an arm left, never what it recorded --------

EDA_BIN = REPO / "bin" / "eda"
GATE_PY = ENGINE / "scripts" / "gate.py"
HARDEN_FINAL = Path("harden") / "runs" / "run" / "final"
# Gates graded only by corpus code against the arm's RTL run first, the
# hardened views next, and every gate that executes the arm's own testbench
# last, so arm code never runs before the corpus graders have read their
# files. A gates.yaml row not named here runs after these, before release.
GATE_ORDER = ("spec_lint", "lint", "holdout", "formal", "synth",
              "harden", "timing", "drc", "lvs", "precheck",
              "sim", "mutate", "cover", "glsim")
HARDENED_GATES = {"harden", "timing", "drc", "lvs", "precheck", "glsim"}
TB_GATES = {"sim", "mutate", "cover", "glsim"}
# what scoring copies from the corpus rung and from the arm; the integrity
# check hashes exactly these trees before and after every gate
CORPUS_PARTS = ("spec", "formal", "holdout")
ARM_PARTS = ("rtl", "tb", str(HARDEN_FINAL))
NOISE = {"__pycache__", ".pytest_cache"}
MAX_FILE_BYTES = 512 * 1024 * 1024
GATE_TIMEOUT_S = 4 * 3600


def _is_link_or_missing(path: Path) -> str | None:
    """Why `path` is not a real directory reached without a link, else None."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if not stat.S_ISDIR(st.st_mode):
        return "not a directory"
    return None


def _real_subdir(root: Path, rel: Path) -> tuple[Path | None, str | None, str]:
    """root/rel when every component below root is a real directory;
    otherwise (None, why, the component that stopped it)."""
    cur = root
    for part in rel.parts:
        cur = cur / part
        why = _is_link_or_missing(cur)
        if why:
            return None, why, str(cur.relative_to(root))
    return cur, None, str(rel)


def find_deliverables_root(ws: Path, names: list[str]) -> tuple[Path, str]:
    """The arm's deliverables sit at the workspace root, or (a skill-arm
    run) under blocks/<name>/: the root when it has rtl/, else blocks/<n>
    for the first of `names` that has one, else the only blocks/* that has
    one. Links are never followed to find it."""
    if _is_link_or_missing(ws):
        raise CheckError(f"--deliverables {ws} is not a real directory "
                         f"({_is_link_or_missing(ws)})")
    if _is_link_or_missing(ws / "rtl") is None:
        return ws, "."
    blocks = ws / "blocks"
    if _is_link_or_missing(blocks) is None:
        cands = [n for n in names if n] + sorted(
            p.name for p in blocks.iterdir() if p.name not in names)
        found = [n for n in dict.fromkeys(cands)
                 if _real_subdir(blocks, Path(n) / "rtl")[0] is not None]
        named = [n for n in found if n in names]
        if named or len(found) == 1:
            n = (named or found)[0]
            return blocks / n, f"blocks/{n}"
    return ws, "."


def copy_regular(src: Path, dst: Path, rel: str, skipped: list[dict]) -> int:
    """Copy the regular files under src into dst; never follow or copy a
    symbolic link (one that dangles in the arm's sandbox can resolve on this
    host - into corpus/, say - and leak the reference into scoring), never
    copy a device, fifo or socket. Each thing left out goes on `skipped`.
    Returns how many files were copied. Files land without an exec bit."""
    n = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        here = Path(dirpath)
        sub = here.relative_to(src)
        keep = []
        for d in sorted(dirnames):
            p = here / d
            if d in NOISE:
                continue
            if os.path.islink(p):
                skipped.append({"path": f"{rel}/{(sub / d).as_posix()}",
                                "why": "symlink"})
            else:
                keep.append(d)
        dirnames[:] = keep
        (dst / sub).mkdir(parents=True, exist_ok=True)
        for f in sorted(filenames):
            p = here / f
            name = f"{rel}/{(sub / f).as_posix()}"
            if f.endswith(".pyc"):
                continue
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                skipped.append({"path": name, "why": "symlink"})
                continue
            if not stat.S_ISREG(st.st_mode):
                skipped.append({"path": name, "why": "not a regular file"})
                continue
            if st.st_size > MAX_FILE_BYTES:
                skipped.append({"path": name, "why": f"over {MAX_FILE_BYTES} bytes"})
                continue
            try:  # O_NOFOLLOW: a file swapped for a link since lstat is refused
                fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            except OSError as exc:
                skipped.append({"path": name, "why": f"unreadable: {exc.strerror}"})
                continue
            with os.fdopen(fd, "rb") as fin, open(dst / sub / f, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            os.chmod(dst / sub / f, 0o644)
            n += 1
    return n


def snapshot(ws: Path) -> dict[str, str]:
    """sha256 of every file the graders and the arm's deliverables hold."""
    out = {}
    for part in CORPUS_PARTS + ARM_PARTS:
        base = ws / part
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if NOISE & set(p.relative_to(ws).parts) or p.suffix == ".pyc":
                continue
            if p.is_symlink():
                out[str(p.relative_to(ws))] = "symlink"
            elif p.is_file():
                out[str(p.relative_to(ws))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _changed(before: dict, after: dict) -> list[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


# ---- the scoring sandbox: every gate on untrusted deliverables -----------

SB_SCORE_HOME = "/home/score"
SB_GATE_OUT = "/score-out"
# repo trees the scoring sandbox masks with an empty tmpfs: the corpus (the
# reference RTL among it), the evals, the repo's tests (corpus copies) and
# docs. engine/ and bin/ stay visible: the gates are engine code.
SANDBOX_MASKED = ("corpus", "evals", "tests", "docs")
SANDBOX_ENV = {"PATH": "/usr/bin:/bin", "HOME": SB_SCORE_HOME, "USER": "score",
               "LOGNAME": "score", "LANG": "C.UTF-8", "TMPDIR": "/tmp",
               "SHELL": "/bin/sh"}
SANDBOX_REMEDIATION = ("install bubblewrap (bwrap) with unprivileged user "
                       "namespaces; --deliverables never runs a gate on an "
                       "arm's code outside it")


def eda_tree() -> Path:
    """The EDA tree bin/eda resolves ($EDA_TOOLCHAIN or its default)."""
    try:
        p = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                           capture_output=True, text=True, timeout=60)
        root = p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        root = ""
    root = root or os.environ.get("EDA_TOOLCHAIN") or ""
    if not root or not Path(root).is_dir():
        raise CheckError(f"no EDA tree ({root or 'unset'}); set EDA_TOOLCHAIN")
    return Path(root)


def _system_binds() -> list[str]:
    args = ["--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc"]
    for name in ("bin", "sbin", "lib", "lib32", "lib64", "libx32"):
        p = Path("/") / name
        if p.is_symlink():
            args += ["--symlink", os.readlink(p), str(p)]
        elif p.is_dir():
            args += ["--ro-bind", str(p), str(p)]
    return args


def gate_sandbox_argv(ws: Path, gate_out: Path, command: list[str],
                      tree: Path | None = None) -> list[str]:
    """The bwrap argv that runs `command` (cwd ws) on a scoring workspace:
    no network, a clean env, the system, EDA tree and repo read-only with
    SANDBOX_MASKED hidden, only ws and gate_out (at SB_GATE_OUT) writable."""
    tree = tree or eda_tree()
    env = {**SANDBOX_ENV, "EDA_TOOLCHAIN": str(tree)}
    argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
            "--hostname", "score", "--clearenv"]
    for k, v in sorted(env.items()):
        argv += ["--setenv", k, v]
    argv += _system_binds()
    argv += ["--proc", "/proc", "--dev", "/dev",
             "--tmpfs", "/tmp", "--tmpfs", "/var/tmp", "--tmpfs", SB_SCORE_HOME]
    pydeps = ttlib._pydeps_cache_root()
    if _is_link_or_missing(pydeps) is None and (pydeps / ".ok").is_file():
        argv += ["--ro-bind", str(pydeps), f"{SB_SCORE_HOME}/.local/state/"
                 f"chip-flow/{pydeps.name}"]
    argv += ["--ro-bind", str(tree), str(tree), "--ro-bind", str(REPO), str(REPO)]
    for m in SANDBOX_MASKED:
        if (REPO / m).exists():
            argv += ["--tmpfs", str(REPO / m)]
    argv += ["--bind", str(ws), str(ws), "--bind", str(gate_out), SB_GATE_OUT,
             "--chdir", str(ws), "--", *command]
    return argv


def sandbox_preflight(ws: Path, outdir: Path) -> dict:
    """Refuse (CheckError, exit 2) unless the scoring sandbox starts and
    hides the corpus: scoring never falls back to an unsandboxed run."""
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise CheckError("no bwrap on PATH: " + SANDBOX_REMEDIATION)
    probe = outdir / "preflight"
    probe.mkdir()
    corpus = REPO / "corpus"
    cmd = ["/bin/sh", "-c", 'test -z "$(ls -A "$1")" && echo ok > "$2"/ok',
           "sh", str(corpus), SB_GATE_OUT]
    try:
        p = subprocess.run(gate_sandbox_argv(ws, probe, cmd),
                           stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckError(f"the scoring sandbox did not start ({exc}): "
                         + SANDBOX_REMEDIATION) from exc
    if p.returncode != 0 or not (probe / "ok").is_file():
        raise CheckError(f"the scoring sandbox did not start or did not hide "
                         f"corpus/ (exit {p.returncode}: {p.stderr[-300:]}): "
                         + SANDBOX_REMEDIATION)
    return {"bwrap": bwrap, "masked": list(SANDBOX_MASKED), "network": "none"}


def run_gate(ws: Path, gate: str, outdir: Path, report: Path | None = None) -> dict:
    """One gate through gate.py in the scoring sandbox (gate_sandbox_argv),
    recorded in the scoring workspace. The result is read from --out, a
    file in outdir/<gate>/, a directory outside the workspace that only
    this gate's sandbox sees, so nothing the arm's code writes into the
    workspace, or wrote during an earlier gate, can stand in for it."""
    gate_out = outdir / gate
    gate_out.mkdir()
    out = gate_out / f"gate-{gate}.json"
    cmd = [str(EDA_BIN), "python", str(GATE_PY), "--gate", gate, "--skill", "vde",
           "--workspace", str(ws), "--out", f"{SB_GATE_OUT}/{out.name}"]
    if report is not None:
        shutil.copyfile(report, gate_out / report.name)
        cmd += ["--report", f"{SB_GATE_OUT}/{report.name}"]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(gate_sandbox_argv(ws, gate_out, cmd),
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=GATE_TIMEOUT_S)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        return {"status": "error", "why": f"timed out after {GATE_TIMEOUT_S}s"}
    wall = round(time.monotonic() - t0, 1)
    try:  # a regular file only: never a link the sandbox planted
        if not stat.S_ISREG(os.lstat(out).st_mode):
            raise OSError("not a regular file")
        fd = os.open(out, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            body = json.loads(f.read(64 * 1024 * 1024).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        return {"status": "error", "wall_s": wall,
                "why": f"gate.py wrote no result (exit {rc}): {proc.stderr[-300:]}"}
    status = body.get("status")
    if status == "error" or rc == 2:
        why = str(body.get("remediation") or body.get("error") or "")
        return {"status": "error", "wall_s": wall, "why": why[-400:]}
    return {"status": status, "wall_s": wall, "failing": body.get("failing_count"),
            "kinds": sorted({v.get("kind") for v in body.get("failing") or []
                             if v.get("kind")}),
            "facts": body.get("facts") or {}}


def harden_artefact_report(ws: Path, top: str, path: Path) -> Path:
    """The harden gate's result for an arm's own LibreLane run, without
    running LibreLane again: check_harden.py's own completeness rule (every
    EXPECTED_FORMATS view in final/) on the copied run. Recorded through
    gate.py --report so release reads it like any other harden result."""
    import check_harden
    final = ws / HARDEN_FINAL
    missing = [f for f in check_harden.EXPECTED_FORMATS
               if not any((final / f).glob("*")) and not (final / f).is_file()]
    violations = [] if not missing else [checklib.violation(
        "harden", "error", None, top, "harden_missing_artifact", [],
        f"the arm's {HARDEN_FINAL} is missing: {missing}", "ladder")]
    try:
        metrics = json.loads((final / "metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metrics = {}
    subset = {k: v for k, v in metrics.items() if ":" not in k and k.startswith(
        ("design__instance", "design__die", "design__core", "timing__setup",
         "timing__hold"))} if isinstance(metrics, dict) else {}
    payload = checklib.report(check_harden.SCRIPT, ws / "rtl", violations, top=top,
                              run_tag="run", metrics=subset,
                              source="the arm's own LibreLane run, not re-hardened")
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


def build_scoring_ws(ws: Path, rung_dir: Path, root: Path, block: str) -> dict:
    """state.py init, the corpus rung's spec.yaml, spec.md, formal/ and
    holdout/, then the arm's rtl/, tb/ and harden/runs/run/final/ (regular
    files only). The arm's own spec/, formal/, state.json and records are
    never copied."""
    import state as state_mod
    state_mod.State.init(ws, "vde", block)
    for f in ("spec.yaml", "spec.md"):
        if (rung_dir / f).is_file():
            shutil.copy2(rung_dir / f, ws / "spec" / f)
    for d in ("formal", "holdout"):
        shutil.rmtree(ws / d, ignore_errors=True)
        if (rung_dir / d).is_dir():
            shutil.copytree(rung_dir / d, ws / d)
        else:
            (ws / d).mkdir()
    skipped: list[dict] = []
    copied = {}
    for rel in ARM_PARTS:
        src, why, where = _real_subdir(root, Path(rel))
        if src is None:
            if why != "missing":
                skipped.append({"path": where, "why": why})
            copied[rel] = 0
            continue
        copied[rel] = copy_regular(src, ws / rel, rel, skipped)
    return {"copied": copied, "skipped": skipped}


def score_deliverables(args) -> tuple[dict, list[dict]]:
    import speclib
    if args.skill != "vde":
        raise CheckError("--deliverables scores vde rungs only: the analog "
                         "held-out swap ade and msde need is not built "
                         "(docs/design-evals.md section 3)")
    if args.rulings:
        raise CheckError("--deliverables never reads the arm's rulings file "
                         "(its spec/ is not copied), so --rulings must be 0")
    rung_dir = CORPUS / args.skill / args.rung
    if not rung_dir.is_dir():
        raise CheckError(f"no corpus rung at corpus/{args.skill}/{args.rung}")
    spec = speclib.load_spec(rung_dir / "spec.yaml")
    top = spec.get("top") or args.rung
    src = Path(args.deliverables).absolute()
    root, root_rel = find_deliverables_root(src, [top, args.rung])
    t0 = time.monotonic()
    rows = gate_mod.load_gates(gate_mod.DEFAULT_GATES).get("vde") or {}
    order = [g for g in GATE_ORDER if g in rows] + [
        g for g in rows if g not in GATE_ORDER and g != "release"]

    tmp = Path(tempfile.mkdtemp(prefix="chip-flow-score-"))
    ws, outdir = tmp / top, tmp / "gate-results"
    outdir.mkdir()
    try:
        copy = build_scoring_ws(ws, rung_dir, root, top)
        hardened = (ws / HARDEN_FINAL).is_dir() and any((ws / HARDEN_FINAL).iterdir())
        has_tb = bool(cocotblib.test_modules(ws / "tb"))
        sandbox = sandbox_preflight(ws, outdir)
        base = snapshot(ws)
        gates: dict[str, dict] = {}
        tampered: list[str] = []
        for g in order + ["release"]:
            if tampered:
                gates[g] = {"status": "not run", "why": "corpus or deliverable "
                            "files changed during scoring"}
            elif g in HARDENED_GATES and not hardened:
                gates[g] = {"status": "not hardened",
                            "why": f"no {HARDEN_FINAL} in the deliverables"}
            elif g in TB_GATES and not has_tb:
                gates[g] = {"status": "no testbench",
                            "why": "no tb/test_*.py in the deliverables"}
            elif g == "harden":
                rep = harden_artefact_report(ws, ttlib.wrapper_name(spec),
                                             outdir / "harden-report.json")
                gates[g] = run_gate(ws, g, outdir, report=rep)
            else:
                gates[g] = run_gate(ws, g, outdir)
            if not tampered:
                tampered = _changed(base, snapshot(ws))
        if args.keep_scoring_ws:
            kept = Path(args.keep_scoring_ws)
            shutil.copytree(tmp, kept, symlinks=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    owed = [g for g in attest.applicable_gates("vde")] + ["release"]
    problems = []
    for g in owed:
        e = gates.get(g) or {"status": "not run", "why": "not in gates.yaml"}
        if e["status"] == "pass":
            continue
        why = {"fail": "last recorded result is FAIL",
               "error": f"did not run: {e.get('why')}",
               }.get(e["status"], e["status"])
        if g == "release":
            why = "no recorded pass"
        problems.append(f"{g}: {why}")
    if tampered:
        problems.append("integrity: files changed during scoring: "
                        + ", ".join(tampered[:10]))
    ho = gates.get("holdout") or {}
    hf = ho.get("facts") or {}
    held = {"status": ho.get("status") if ho.get("status") in ("pass", "fail")
            else "error", "tests_passed": hf.get("tests_passed"),
            "tests_run": len(hf.get("tests_run") or []) if hf else None,
            "kinds": ho.get("kinds") or []}
    if held["status"] == "error":
        held["why"] = ho.get("why")
    fact = lambda g, k: ((gates.get(g) or {}).get("facts") or {}).get(k)  # noqa: E731
    counts = (not problems and held["status"] == "pass" and args.hand_edits == 0)
    result = {
        "skill": args.skill, "rung": args.rung, "block": top,
        "run": src.name, "phase": None,
        "scored_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "counts": counts, "gates_green": not problems,
        "gate_problems": problems,
        "gates_passed": sorted(g for g, e in gates.items() if e["status"] == "pass"),
        "gates": {g: {k: v for k, v in e.items() if k != "facts"}
                  for g, e in gates.items()},
        "hand_edits": args.hand_edits, "rulings": 0, "rulings_undeclared": 0,
        "held_out": held,
        "hardened": hardened, "testbench": has_tb,
        "kill_rate": fact("mutate", "kill_rate"),
        "line_pct": fact("cover", "line_pct"),
        "area": fact("synth", "area"),
        "worst_slack_ns": worst_slack(fact("timing", "corners")),
        "fix_attempts": None,
        "tokens": args.session_tokens,
        "cost_usd": round(args.session_cost_usd, 2)
        if args.session_cost_usd is not None else None,
        "wall_s": round(args.wall_s) if args.wall_s is not None else None,
        "scoring_wall_s": round(time.monotonic() - t0),
        "deliverables": {"root": root_rel, **copy,
                         "not_scored": ["the arm's own spec/, formal/, state.json "
                                        "and gate records"]},
        "integrity": "ok" if not tampered else tampered,
        "sandbox": sandbox,
        "note": args.note,
        "kind": "round",
    }
    result.update(provenance(args, "deliverables"))
    violations = [checklib.violation("ladder", "error", None, args.rung,
                                     "gate_not_green", [], p, "ladder")
                  for p in problems]
    if held["status"] != "pass":
        violations.append(checklib.violation(
            "ladder", "error", "holdout", args.rung, "holdout_failed", [],
            f"the corpus held-out tests {held['status']}"
            + (f" ({', '.join(held['kinds'])})" if held["kinds"] else ""), "holdout"))
    if args.hand_edits:
        violations.append(checklib.violation(
            "ladder", "error", None, args.rung, "hand_edits", [],
            f"{args.hand_edits} hand edit(s) declared", "caller"))
    return result, violations


# ---- ladder.md -------------------------------------------------------------

def latest(dirpath: Path, pattern: str) -> Path | None:
    files = sorted(dirpath.glob(pattern)) if dirpath.is_dir() else []
    return files[-1] if files else None


def is_full_cvdp(c: dict) -> bool:
    """A full-subset run: the pinned benchmark file, no --category, no
    --limit, every subset problem selected. A smoke, category or example
    (positive-control) run must not stand in for the number the page reports."""
    return ((c.get("dataset") or {}).get("file") == CVDP_PINNED_FILE
            and c.get("categories") is None and c.get("limit") is None
            and c.get("selected") is not None
            and c.get("selected") == c.get("subset_size"))


def latest_cvdp(dirpath: Path) -> Path | None:
    """The newest full-subset CVDP run, else the newest run of any kind."""
    files = sorted(dirpath.glob("*.json")) if dirpath.is_dir() else []
    full = [f for f in files
            if is_full_cvdp(json.loads(f.read_text(encoding="utf-8")))]
    return (full or files or [None])[-1]


def latest_ladder_results(results: Path, kind: str = "skill") -> dict:
    """Newest result per (skill, rung) of one kind: "skill" runs (a result
    written before `kind` existed counts as one) or "reference" runs."""
    out = {}
    for p in sorted((results / "ladder").glob("*.json")) if (results / "ladder").is_dir() else []:
        r = json.loads(p.read_text(encoding="utf-8"))
        if r.get("kind", "skill") == kind:
            out[(r["skill"], r["rung"])] = r  # sorted by dated name: newest wins
    return out


def latest_round_results(results: Path) -> dict:
    """Newest deliverables-scored result per (skill, rung, arm, detail,
    repeat)."""
    out = {}
    d = results / "ladder"
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        r = json.loads(p.read_text(encoding="utf-8"))
        if r.get("kind") == "round":
            out[(r["skill"], r["rung"], r.get("arm"), r.get("detail"),
                 r.get("repeat"))] = r
    return out


def _cell(ladder_row: dict, skill: str, res: dict | None) -> str:
    title = ladder_row["title"]
    if not (CORPUS / skill / ladder_row["rung"]).is_dir():
        return f"{title}: not in corpus"
    if res is None:
        return f"{title}: not run"
    return f"{title}: {'counts' if res['counts'] else 'red'}"


def _fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _why_red(r: dict) -> str:
    if r["counts"]:
        return ""
    bits = []
    if not r["gates_green"]:
        bits.append(f"{len(r['gate_problems'])} gate(s) not green")
    if r["held_out"]["status"] == "fail":
        bits.append("held-out fails")
    elif r["held_out"]["status"] == "error":
        bits.append("held-out did not run")
    if r["hand_edits"]:
        bits.append("hand edits")
    if r.get("rulings_undeclared"):
        bits.append("undeclared rulings")
    return "; ".join(bits)


TABLE_HEAD = ["| rung | counts | why not | held-out | rulings | kill rate | area "
              "| worst slack ns | fix attempts | tokens | cost USD | wall s | scored | note |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]


def _row(rung: str, r: dict) -> str:
    h = r["held_out"]
    held = h["status"] if h.get("tests_run") is None else \
        f"{h['status']} ({h.get('tests_passed')}/{h.get('tests_run')})"
    return "| " + " | ".join([
        rung, "yes" if r["counts"] else "no", _why_red(r), held, _fmt(r.get("rulings")),
        _fmt(r.get("kill_rate")), _fmt(r.get("area"), 1),
        _fmt(r.get("worst_slack_ns")), _fmt(r.get("fix_attempts")),
        _fmt(r.get("tokens")), _fmt(r.get("cost_usd")), _fmt(r.get("wall_s")),
        r["scored_at"][:10], r.get("note") or ""]) + " |"


def render(results: Path, fixtures: Path) -> str:
    ladder = load_ladder()
    res = latest_ladder_results(results)
    skills = ["vde", "ade", "msde"]
    lines = [
        "# The ladder",
        "",
        "Generated by `evals/ladder.py --regen` from `evals/results/`; do not edit by hand.",
        "A rung counts when every gate is green, the corpus held-out tests pass and nobody",
        "edited the run by hand (docs/design.md section 3). The owner's per-mutant rulings",
        "are counted in their own column and are not hand edits. Harder rungs are higher up.",
        "",
        "| level | vde | ade | msde |",
        "|---|---|---|---|",
    ]
    depth = max(len(ladder.get(s) or []) for s in skills)
    for i in reversed(range(depth)):
        cells = []
        for s in skills:
            rows = ladder.get(s) or []
            cells.append(_cell(rows[i], s, res.get((s, rows[i]["rung"])))
                         if i < len(rows) else "")
        lines.append(f"| {i + 1} | " + " | ".join(cells) + " |")

    for s in skills:
        lines += ["", f"## /{s}", ""]
        scored = [r for r in (ladder.get(s) or []) if (s, r["rung"]) in res]
        if not scored:
            lines.append(f"No /{s} run scored yet. `ladder.py --skill {s} --rung <rung> "
                         "--run <ws>` fills this table.")
            continue
        lines += TABLE_HEAD
        for row in ladder.get(s) or []:
            r = res.get((s, row["rung"]))
            lines.append(_row(row["rung"], r) if r else
                         f"| {row['rung']} | not run |" + " |" * 12)

    refs = latest_ladder_results(results, "reference")
    if refs:
        lines += ["", "## Reference baselines", "",
                  "The corpus reference solutions scored the same way (`ladder.py "
                  "--reference`). These are not skill runs and never fill a column above; "
                  "they say what a rung costs when the design is right.", "",
                  "| skill | " + TABLE_HEAD[0][2:], "|---" + TABLE_HEAD[1]]
        for s in skills:
            for row in ladder.get(s) or []:
                if (s, row["rung"]) in refs:
                    lines.append(f"| {s} " + _row(row["rung"], refs[(s, row["rung"])]))

    rounds = latest_round_results(results)
    if rounds:
        lines += ["", "## Bare against skill rounds", "",
                  "Each arm's deliverables scored in a fresh scoring workspace "
                  "(`ladder.py --deliverables`), never from its own record: the "
                  "corpus held-out tests and properties on its RTL, mutate and cover "
                  "on its own testbench, signoff on its own hardened run "
                  "(docs/design-evals.md section 3). These never fill a column above. "
                  "`scorecard.py --round` pairs them.", "",
                  "| rung | arm | detail | repeat | model | counts | why not | held-out "
                  "| kill rate | line % | area | worst slack ns | hardened | testbench "
                  "| cost USD | wall s | scored |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for key in sorted(rounds, key=lambda k: tuple(str(x) for x in k)):
            r = rounds[key]
            h = r["held_out"]
            held = h["status"] if h.get("tests_run") is None else \
                f"{h['status']} ({h.get('tests_passed')}/{h.get('tests_run')})"
            lines.append("| " + " | ".join([
                f"{r['skill']}/{r['rung']}", str(r.get("arm")), str(r.get("detail")),
                _fmt(r.get("repeat")), r.get("model") or "-",
                "yes" if r["counts"] else "no", _why_red(r), held,
                _fmt(r.get("kill_rate")), _fmt(r.get("line_pct"), 1),
                _fmt(r.get("area"), 1), _fmt(r.get("worst_slack_ns")),
                "yes" if r.get("hardened") else "not hardened",
                "yes" if r.get("testbench") else "no testbench",
                _fmt(r.get("cost_usd")), _fmt(r.get("wall_s")),
                r["scored_at"][:10]]) + " |")

    lines += ["", "## Per-stage benches", "",
              "Frozen fixtures under `evals/fixtures/<stage>/<name>/`; `bench.py --compare` "
              "fails a change that lowers a composite.", ""]
    bases = sorted(fixtures.glob("*/*/baseline.json")) if fixtures.is_dir() else []
    if not bases:
        lines.append("No baselines yet.")
    else:
        lines += ["| stage | fixture | baseline composite | gates | written |",
                  "|---|---|---|---|---|"]
        for b in bases:
            d = json.loads(b.read_text(encoding="utf-8"))
            gs = ", ".join(f"{g} {v['status']}" for g, v in d["gates"].items())
            lines.append(f"| {b.parent.parent.name} | {b.parent.name} | "
                         f"{d['composite']} | {gs} | {d.get('written', '-')} |")

    lines += ["", "## CVDP", ""]
    cv = latest_cvdp(results / "cvdp")
    if cv is None:
        lines.append("No CVDP result yet (`evals/cvdp/run.py`).")
    else:
        c = json.loads(cv.read_text(encoding="utf-8"))
        ov = c.get("overall") or {}
        shown = ("Newest full-subset run" if is_full_cvdp(c)
                 else "Newest run (no full-subset run yet)")
        lines += [f"{shown}: `{cv.name}`, mode `{c.get('mode', '-')}`: "
                  f"{ov.get('pass', '-')} of {ov.get('total', '-')} passed "
                  f"({_fmt(ov.get('pass_rate'))}), from a subset of "
                  f"{c.get('subset_size', '-')} of {c.get('dataset_size', '-')} "
                  f"problems (limit {c.get('limit') or 'none'}).", ""]
        if c.get("mode_note"):
            lines += [f"Mode `{c['mode']}`: {c['mode_note']}.", ""]
        if c.get("caveat"):
            lines += [f"Caveat: {c['caveat']}", ""]
        cats = c.get("by_category") or {}
        if cats:
            lines += ["| category | passed | total | rate |", "|---|---|---|---|"]
            for cat, v in sorted(cats.items()):
                lines.append(f"| {cat} {v.get('name', '')} | {v.get('pass')} | "
                             f"{v.get('total')} | {_fmt(v.get('pass_rate'))} |")
        lines += ["", "The subset rule, and where the leaderboard figures to "
                  "read this beside go, are in `evals/cvdp/README.md`."]
    return "\n".join(lines) + "\n"


def regen(results: Path = RESULTS, fixtures: Path = EVALS / "fixtures",
          out: Path = EVALS / "ladder.md") -> Path:
    out.write_text(render(results, fixtures), encoding="utf-8")
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--regen", action="store_true", help="only rebuild ladder.md")
    ap.add_argument("--skill", choices=("vde", "ade", "msde"))
    ap.add_argument("--rung")
    ap.add_argument("--run", help="the run's block workspace (scored from its record)")
    ap.add_argument("--deliverables", metavar="WS",
                    help="an arm's workspace, scored from its deliverables only "
                    "(rtl/, tb/, harden/runs/run/final/) in a fresh scoring workspace")
    ap.add_argument("--arm", choices=("bare", "skill"), default="skill")
    ap.add_argument("--detail", choices=("terse", "typical", "full"), default="typical")
    ap.add_argument("--repeat", type=int, help="the repeat number within a cell")
    ap.add_argument("--model", help="the model id the run used")
    ap.add_argument("--hand-edits", type=int,
                    help="hand edits made during the run (required to score)")
    ap.add_argument("--rulings", type=int, default=0,
                    help="how many of the run's spec/mutant_rulings.yaml entries the "
                    "owner wrote (the rest are scored as hand edits)")
    ap.add_argument("--session-tokens", type=int)
    ap.add_argument("--session-cost-usd", type=float)
    ap.add_argument("--wall-s", type=float)
    ap.add_argument("--note", help="what this run was, shown on ladder.md "
                    "(e.g. that it is not a skill run)")
    ap.add_argument("--reference", action="store_true",
                    help="the run is not a skill session (e.g. the corpus reference "
                    "RTL): listed as a reference baseline, never on the skill column")
    ap.add_argument("--keep-scoring-ws", metavar="DIR",
                    help="--deliverables: keep a copy of the scoring workspace here")
    ap.add_argument("--no-regen", action="store_true",
                    help="write the result but leave ladder.md alone")
    ap.add_argument("--results-dir", default=str(RESULTS))
    ap.add_argument("--fixtures-dir", default=str(EVALS / "fixtures"))
    ap.add_argument("--ladder-md", default=str(EVALS / "ladder.md"))
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)
    results = Path(args.results_dir)

    if args.regen:
        md = regen(results, Path(args.fixtures_dir), Path(args.ladder_md))
        return {"script": SCRIPT, "status": "pass", "ladder_md": md.name}, args.out
    if args.run and args.deliverables:
        raise CheckError("give --run (score a run's record) or --deliverables "
                         "(score an arm's deliverables), not both")
    if args.deliverables and args.reference:
        raise CheckError("--reference marks a --run record; a --deliverables "
                         "result is always a round result")
    if args.repeat is not None and args.repeat < 1:
        raise CheckError("--repeat counts from 1")
    missing = [f for f in ("skill", "rung", "hand_edits") if getattr(args, f) is None]
    if not (args.run or args.deliverables):
        missing.insert(2, "run")
    if missing:
        raise CheckError("scoring a run needs " + ", ".join(
            "--" + m.replace("_", "-") for m in missing) + " (or --regen)")
    result, violations = (score_deliverables(args) if args.deliverables
                          else score(args))
    (results / "ladder").mkdir(parents=True, exist_ok=True)
    res_path = results / "ladder" / result_name(args)
    res_path.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    if not args.no_regen:
        regen(results, Path(args.fixtures_dir), Path(args.ladder_md))
    payload = {"script": SCRIPT, "status": "violations" if violations else "pass",
               "counts": checklib.summarize(violations), "violations": violations,
               "result": result, "result_file": res_path.name}
    return payload, args.out


def result_name(args, stamp: str | None = None) -> str:
    """<stamp>_<skill>_<rung>.json; a --deliverables result, or one given
    --repeat, adds _<arm>_<detail>_r<repeat> so a round's results never
    collide."""
    stamp = stamp or dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    name = f"{stamp}_{args.skill}_{args.rung}"
    if args.deliverables or args.repeat is not None:
        name += f"_{args.arm}_{args.detail}_r{args.repeat if args.repeat is not None else 0}"
    return name + ".json"


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
