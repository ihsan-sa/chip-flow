#!/usr/bin/env python
"""run.py - the CVDP public number (docs/design.md section 3, "### M6.").

    bin/eda python evals/cvdp/run.py [--limit N] [--category cid003 ...]
        [--solutions DIR] [--dataset nonagentic|example] [--jobs 4]
        [--timeout 300] [--select-only] [--out FILE]

Fetches NVIDIA's CVDP v1.1 dataset at a pinned revision (cached outside the
repo, verified by sha256), selects the problems whose harness needs only
Icarus and cocotb (the rule is spelled out in evals/cvdp/README.md and in
`select()` below), runs each problem's docker-compose services natively
under `bin/eda` in a scratch directory, and scores pass rate overall and by
category. The result is written dated under evals/results/cvdp/.

What is scored (`mode` in the result):
  reference  the rows carry their own solutions (only the `example` dataset
             does; the public v1.1 benchmark ships none)
  solutions  files from --solutions DIR, laid out DIR/<problem id>/<path>
  null       neither: the problem's own input files, unchanged. A floor,
             and a harness-reach check: `tests_ran` says how many problems
             got as far as executing their cocotb tests.

Exit: 0 the run completed and every problem reached a verdict (pass or
fail are data, not findings); 1 it completed but some problems hit a
runner-side error (each is a finding); 2 it could not run at all.

This is not the official harness: problems run natively under `eda`, not in
their containers, and a leaderboard submission needs the docker harness.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "engine" / "lib"))
import checklib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "cvdp_run"
EDA = REPO / "bin" / "eda"
RESULTS = REPO / "evals" / "results" / "cvdp"

HF_REPO = "nvidia/cvdp-benchmark-dataset"
HF_REVISION = "5b807d945f6a99aa645f7e43a64a2115e281b4bf"
GH_REPO = "NVlabs/cvdp_benchmark"
GH_COMMIT = "8e894cf74414ab1eaea1e2b4e80a02f123df07b6"

DATASETS = {
    # The benchmark: non-agentic code generation, non-commercial tools.
    "nonagentic": {
        "source": f"huggingface:{HF_REPO}@{HF_REVISION}",
        "file": "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl",
        "url": (f"https://huggingface.co/datasets/{HF_REPO}/resolve/"
                f"{HF_REVISION}/cvdp_v1.1.0_nonagentic_code_generation_"
                "no_commercial.jsonl"),
        "sha256": "cbcd81295561ebb16e4d857e096f4d9908d042c33aff3b58abf236e868411857",
    },
    # The one public non-agentic problem that ships its solution: the
    # positive control that a correct answer passes the native runner.
    "example": {
        "source": f"github:{GH_REPO}@{GH_COMMIT}",
        "file": ("cvdp_v1.1.0_example_nonagentic_code_generation_"
                 "no_commercial_with_solutions.jsonl"),
        "url": (f"https://raw.githubusercontent.com/{GH_REPO}/{GH_COMMIT}/"
                "example_dataset/cvdp_v1.1.0_example_nonagentic_code_"
                "generation_no_commercial_with_solutions.jsonl"),
        "sha256": "ab4a25cbc7c9827db560e45219058e313081a83f4859b9d8e2c84fd832f258ef",
    },
}

# Code-generation categories whose answer is RTL a harness simulates.
CATEGORIES = {
    "cid002": "code completion",
    "cid003": "spec to RTL",
    "cid004": "code modification",
    "cid007": "code improvement",
    "cid016": "bug fixing",
}
BORROW_FROM = {"cid004", "cid007"}
MAX_DONORS = 2
IMAGES = {"__OSS_SIM_IMAGE__", "__OSS_PNR_IMAGE__"}
# Dockerfile lines that only add pytest, which `eda python` already has.
DOCKERFILE_OK = re.compile(
    r"^(FROM\s+__OSS_(SIM|PNR)_IMAGE__(\s+AS\s+\w+)?"
    r"|ADD\s+https://bootstrap\.pypa\.io/\S*get-pip\.py\s+get-pip\.py"
    r"|RUN\s+python3\s+\./get-pip\.py"
    r"|RUN\s+python3\s+-m\s+pip\s+install\s+pytest(==[\w.]+)?)$", re.I)
COMMERCIAL = re.compile(r"\b(xrun|irun|xcelium|vcs|vsim|questa|modelsim|"
                        r"jasper|jaspergold|spyglass|genus|innovus)\b", re.I)
CAVEAT = ("Not the official CVDP harness: each problem's docker-compose "
          "services run natively under bin/eda (Icarus, cocotb) instead of in "
          "their containers, with container paths rewritten into a scratch "
          "directory, on a subset of the non-agentic non-commercial set. Tool "
          "versions differ from the dataset's pinned images. A leaderboard "
          "submission needs the docker harness and is out of scope.")
MODE_NOTE = {
    "reference": "the dataset's own reference solutions",
    "solutions": "solutions supplied with --solutions",
    "borrowed": ("each problem scored with the working RTL a sibling problem "
                 "of its family ships (a cid004/cid007 problem's code before "
                 "the change), first of up to two siblings to pass. Only "
                 "problems with such a sibling are run. A pass shows the native "
                 "harness can pass the problem; a fail may be the older code "
                 "not meeting the newer spec"),
    "null": ("no solutions: each problem's own input files, unchanged. The "
             "public v1.1 set ships no reference solutions, so this is a "
             "floor plus a harness-reach check (tests_ran), not a model score"),
}


# ------------------------------------------------------------------ fetch

def cache_dir() -> Path:
    if os.environ.get("CVDP_CACHE"):
        return Path(os.environ["CVDP_CACHE"])
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "chip-flow" / "cvdp"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(name: str) -> Path:
    """The pinned dataset file, downloaded once into the cache and checked
    against its sha256 every time it is used."""
    ds = DATASETS[name]
    dest = cache_dir() / ds["sha256"][:12] / ds["file"]
    if dest.is_file() and sha256(dest) == ds["sha256"]:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        with urllib.request.urlopen(ds["url"], timeout=120) as resp, \
                open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    except OSError as exc:
        raise CheckError(f"cannot download {ds['url']}: {exc}; check the "
                         "network, or copy the file into "
                         f"{dest.parent} by hand") from exc
    got = sha256(tmp)
    if got != ds["sha256"]:
        tmp.unlink(missing_ok=True)
        raise CheckError(f"{ds['file']} sha256 {got} does not match the pin "
                         f"{ds['sha256']}; the source changed under a pinned "
                         "revision - re-pin deliberately, do not bypass")
    tmp.replace(dest)
    return dest


def load_rows(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise CheckError(f"{path}:{n} is not JSON: {exc}") from exc
    return rows


# ------------------------------------------------------------------ select

def parse_env(text: str) -> dict:
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def _live_source(text: str) -> str:
    """A harness .py with the functions nothing calls cut out, so a dead
    helper (several harness libraries define an unused `xrun_tb`) does not
    count as needing that tool."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    lines = text.splitlines()
    for node in sorted((n for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and n.name not in names
                        and not n.name.startswith("test")),
                       key=lambda n: -n.lineno):
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        del lines[start - 1:node.end_lineno]
    return "\n".join(lines)


def services(row: dict) -> list[dict]:
    """The row's docker-compose services as {name, image, command, workdir,
    env_files}; raises ValueError when the compose file is unusable."""
    import yaml
    files = row["harness"]["files"]
    doc = yaml.safe_load(files.get("docker-compose.yml", "")) or {}
    out = []
    for name, svc in (doc.get("services") or {}).items():
        svc = svc or {}
        image = svc.get("image")
        dockerfile = None
        if image is None and "build" in svc:
            b = svc["build"]
            if isinstance(b, str):
                ctx, dfile = b, "Dockerfile"
            else:
                ctx, dfile = b.get("context", "."), b.get("dockerfile", "Dockerfile")
            dockerfile = os.path.normpath(os.path.join(ctx, dfile))
        env_files = svc.get("env_file") or []
        if isinstance(env_files, str):
            env_files = [env_files]
        out.append({"name": name, "image": image, "dockerfile": dockerfile,
                    "command": svc.get("command"),
                    "workdir": svc.get("working_dir"),
                    "env_files": [os.path.normpath(e) for e in env_files]})
    if not out:
        raise ValueError("no services")
    return out


def argv_of(command) -> list[str]:
    """A compose command as argv, unwrapping `sh -c "..."`."""
    argv = command if isinstance(command, list) else shlex.split(str(command or ""))
    if len(argv) == 3 and argv[0] in ("sh", "/bin/sh", "bash", "/bin/bash") \
            and argv[1] == "-c":
        argv = shlex.split(argv[2])
    return argv


def exclusion(row: dict) -> str | None:
    """Why a problem is outside the subset, or None when it is in. The
    subset: a code-generation category, every compose service on the stock
    open-source sim image (or a Dockerfile that only adds pytest), a plain
    `pytest`/`python3` command, SIM=icarus, and no harness file that
    invokes a commercial tool, Verilator or yosys."""
    cats = row.get("categories") or []
    if not cats or cats[0] not in CATEGORIES:
        return "category"
    files = row.get("harness", {}).get("files", {})
    try:
        svcs = services(row)
    except Exception:  # noqa: BLE001
        return "compose"
    for s in svcs:
        if s["image"] is not None:
            if str(s["image"]).split("#")[0].strip() not in IMAGES:
                return "image"
        elif s["dockerfile"]:
            text = files.get(s["dockerfile"])
            if text is None:
                return "dockerfile-missing"
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not DOCKERFILE_OK.match(line):
                    return "dockerfile-extra"
        else:
            return "image"
        argv = argv_of(s["command"])
        if not argv or argv[0] not in ("pytest", "python3", "python") or \
                any(t in ("&&", ";", "|", "||") for t in argv):
            return "command"
    env = {}
    for s in svcs:
        for e in s["env_files"]:
            env.update(parse_env(files.get(e, "")))
    if env.get("SIM", "icarus").lower() != "icarus":
        return "simulator"
    live = "\n".join(_live_source(v) if k.endswith(".py") else v
                     for k, v in files.items() if k.startswith("src/"))
    if COMMERCIAL.search(live):
        return "commercial-tool"
    if re.search(r"\bverilator\b", live, re.I):
        return "verilator"
    if re.search(r"\byosys\b", live, re.I):
        return "yosys"
    return None


def select(rows: list[dict], categories=None, limit=None, ids=None):
    """(selected rows in id order, {excluded id: reason})."""
    chosen, excluded = [], {}
    for row in sorted(rows, key=lambda r: r["id"]):
        why = exclusion(row)
        if why:
            excluded[row["id"]] = why
        else:
            chosen.append(row)
    if categories:
        chosen = [r for r in chosen if r["categories"][0] in categories]
    if ids:
        chosen = [r for r in chosen if r["id"] in ids]
    if limit is not None:
        chosen = chosen[:limit]
    return chosen, excluded


def has_solution(row: dict) -> bool:
    ctx = (row.get("output") or {}).get("context") or {}
    return bool(ctx) and all(v for v in ctx.values())


# ------------------------------------------------------------------ run

PATH_RE = re.compile(r"(?<![\w./-])/(code|src|rundir)(?=/|\b)")


def rewrite(text: str, root: Path) -> str:
    """Container paths (/code, /src, /rundir) into the scratch root."""
    return PATH_RE.sub(lambda m: f"{root}/{m.group(1)}", text)


def _safe_rel(rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"unsafe path in dataset: {rel}")
    return p


def stage(row: dict, root: Path, overlay: dict) -> None:
    """Lay the problem out under root the way its containers see it:
    root/code (the problem's input files, then the candidate's on top),
    root/src (the harness, container paths rewritten)."""
    code = root / "code"
    (code / "rundir").mkdir(parents=True)
    (root / "rundir" / "harness").mkdir(parents=True)
    ctx = dict((row.get("input") or {}).get("context") or {})
    ctx.update(overlay)
    for rel, body in ctx.items():
        dst = code / _safe_rel(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            dst.write_bytes(body)
        else:
            dst.write_text(body, encoding="utf-8")
    for rel, text in row["harness"]["files"].items():
        if rel.startswith("src/"):
            dst = root / _safe_rel(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(rewrite(text, root), encoding="utf-8")


def sources(row: dict) -> list[str]:
    """The design files the harness compiles, relative to /code."""
    env = {}
    for s in services(row):
        for e in s["env_files"]:
            env.update(parse_env(row["harness"]["files"].get(e, "")))
    return [re.sub(r"^/code/", "", p)
            for p in env.get("VERILOG_SOURCES", "").split()]


def family(pid: str) -> str:
    return re.sub(r"_\d+$", "", pid)


def donors(rows: list[dict], chosen: list[dict]) -> dict:
    """For --borrow: {problem id: [sibling ids]} - problems of the same
    family (id without its _NNNN) in a category whose shipped input is
    working RTL (cid004 code modification, cid007 code improvement: the
    code before the change), which ship every file this problem's harness
    compiles. Their RTL is a correct answer to an earlier spec, so it may
    pass this one: evidence the native harness can pass the problem."""
    fams: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["categories"][0] in BORROW_FROM:
            fams.setdefault(family(r["id"]), []).append(r)
    out = {}
    for r in chosen:
        need = sources(r)
        found = [o["id"] for o in fams.get(family(r["id"]), [])
                 if o["id"] != r["id"] and need and
                 all((o["input"].get("context") or {}).get(n) for n in need)]
        if found:
            out[r["id"]] = found[:MAX_DONORS]
    return out


def candidates(row: dict, mode: str, solutions: Path | None,
               donor_rows: list[dict]) -> list[tuple[str, dict]]:
    """[(label, files laid over the problem's input)] to try in order; the
    problem passes on the first that passes. Empty: nothing to score."""
    if mode == "null":
        return [("input", {})]
    if mode == "reference":
        return [("reference", row["output"]["context"])]
    if mode == "borrowed":
        return [(d["id"], {k: v for k, v in d["input"]["context"].items()
                           if k.startswith("rtl/")}) for d in donor_rows]
    src = solutions / row["id"]
    if not src.is_dir():
        return []
    return [("solutions", {str(f.relative_to(src)): f.read_bytes()
                           for f in sorted(src.rglob("*")) if f.is_file()})]


def eda_argv(argv: list[str], root: Path) -> list[str]:
    args = [rewrite(a, root) for a in argv[1:]]
    if argv[0] == "pytest":
        return [str(EDA), "python", "-m", "pytest", *args]
    return [str(EDA), "python", *args]


def run_service(svc: dict, row: dict, root: Path, timeout: float) -> dict:
    files = row["harness"]["files"]
    env = dict(os.environ)
    for e in svc["env_files"]:
        env.update({k: rewrite(v, root)
                    for k, v in parse_env(files.get(e, "")).items()})
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (env.get("PYTHONPATH"), str(root / "src")) if p)
    env.pop("PYTEST_ADDOPTS", None)
    cwd = Path(rewrite(svc["workdir"], root)) if svc["workdir"] else root / "code" / "rundir"
    cwd.mkdir(parents=True, exist_ok=True)
    argv = eda_argv(argv_of(svc["command"]), root)
    t0 = time.monotonic()
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        rc = None
    log = out.decode("utf-8", "replace")
    # cocotb's runner sends the compiler's own messages to sim.log, not to
    # pytest's output: fold them in so a compile failure says why.
    for f in sorted(root.rglob("sim.log")):
        log += f"\n--- {f.relative_to(root)} ---\n" + \
            f.read_text(encoding="utf-8", errors="replace")[-4000:]
    (root / f"{svc['name']}.log").write_text(log, encoding="utf-8")
    return {"service": svc["name"], "rc": rc, "log": log,
            "wall_s": round(time.monotonic() - t0, 1)}


RUNNER_FAULT = re.compile(
    r"(ModuleNotFoundError: No module named '(?!harness_library)[^']+'"
    r"|INTERNALERROR|command not found|ERROR: file or directory not found"
    r"|No such file or directory: '(iverilog|vvp|yosys|pytest)'"
    r"|eda: .*(not found|unknown tool))")
COMPILE = re.compile(r"(\berror:|syntax error|Unknown module type|"
                     r"Unable to find the root module|No such file or directory|"
                     r"I give up|Elaboration failed)", re.I)
COCOTB_SUMMARY = re.compile(r"TESTS=(\d+) PASS=(\d+) FAIL=(\d+)")


def classify(results: list[dict]) -> tuple[str, str, bool]:
    """(status, reason, tests_ran) from a problem's service runs."""
    logs = "\n".join(r["log"] for r in results)
    tests_ran = bool(COCOTB_SUMMARY.search(logs))
    if all(r["rc"] == 0 for r in results):
        return "pass", "", tests_ran
    bad = next(r for r in results if r["rc"] != 0)
    if bad["rc"] is None:
        return "fail", f"timeout in {bad['service']}", tests_ran
    m = RUNNER_FAULT.search(bad["log"])
    if m or bad["rc"] in (3, 4):
        return "error", (m.group(0) if m else f"pytest exit {bad['rc']}")[:200], tests_ran
    if bad["rc"] == 5:
        return "error", "pytest collected no tests", tests_ran
    sums = COCOTB_SUMMARY.findall(bad["log"])
    if any(int(f) > 0 for _, _, f in sums):
        return "fail", f"cocotb tests failed in {bad['service']}", True
    m = COMPILE.search(bad["log"])
    if m:
        line = next((ln for ln in bad["log"].splitlines() if m.group(0) in ln), m.group(0))
        return "fail", f"compile: {line.strip()[:180]}", tests_ran
    return "fail", f"{bad['service']} exit {bad['rc']}", tests_ran


def attempt(row: dict, overlay: dict, timeout: float, keep: Path | None) -> dict:
    root = Path(tempfile.mkdtemp(prefix=f"cvdp-{row['id']}-",
                                 dir=str(keep) if keep else None))
    try:
        stage(row, root, overlay)
        results = [run_service(s, row, root, timeout) for s in services(row)]
        status, reason, ran = classify(results)
        rec = {"status": status, "reason": reason, "tests_ran": ran,
               "services": {r["service"]: r["rc"] for r in results}}
        if status != "pass":
            bad = next((r for r in results if r["rc"] != 0), results[-1])
            rec["log_tail"] = bad["log"][-800:]
        return rec
    finally:
        if keep is None:
            shutil.rmtree(root, ignore_errors=True)


def run_problem(row: dict, tries: list[tuple[str, dict]], timeout: float,
                keep: Path | None) -> dict:
    t0 = time.monotonic()
    rec = {"id": row["id"], "category": row["categories"][0],
           "difficulty": (row["categories"][1:] or [None])[0]}
    if not tries:
        rec.update(status="fail", reason="no solution supplied",
                   tests_ran=False, wall_s=0.0)
        return rec
    for label, overlay in tries:
        try:
            res = attempt(row, overlay, timeout, keep)
        except Exception as exc:  # noqa: BLE001  one problem never sinks the run
            res = {"status": "error", "tests_ran": False,
                   "reason": f"{type(exc).__name__}: {exc}"[:200]}
        rec.update(res, candidate=label)
        if res["status"] == "pass":
            break
    rec["tried"] = len(tries)
    rec["wall_s"] = round(time.monotonic() - t0, 1)
    return rec


# ------------------------------------------------------------------ score

def rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def aggregate(records: list[dict]) -> dict:
    """Pass rate overall and by category. Runner errors count in the
    denominator: a problem we could not run is not a pass."""
    def tally(recs):
        c = {s: sum(1 for r in recs if r["status"] == s)
             for s in ("pass", "fail", "error")}
        c["total"] = len(recs)
        c["tests_ran"] = sum(1 for r in recs if r.get("tests_ran"))
        c["pass_rate"] = rate(c["pass"], c["total"])
        return c
    cats = sorted({r["category"] for r in records})
    return {"overall": tally(records),
            "by_category": {c: {"name": CATEGORIES.get(c, c),
                                **tally([r for r in records if r["category"] == c])}
                            for c in cats}}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", choices=sorted(DATASETS), default="nonagentic")
    ap.add_argument("--dataset-file",
                    help="use this local jsonl instead of the pinned download "
                         "(recorded as unpinned)")
    ap.add_argument("--solutions", help="DIR/<problem id>/<path> candidate files")
    ap.add_argument("--borrow", action="store_true",
                    help="score each problem with a sibling problem's shipped "
                         "working RTL (a harness-fidelity check, see README)")
    ap.add_argument("--category", action="append",
                    help="only this category (repeatable), e.g. cid003")
    ap.add_argument("--id", action="append", dest="ids",
                    help="only this problem id (repeatable)")
    ap.add_argument("--limit", type=int, help="first N of the subset, by id")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=300.0,
                    help="seconds per compose service")
    ap.add_argument("--select-only", action="store_true",
                    help="report the subset without running anything")
    ap.add_argument("--keep", help="keep scratch directories (and each "
                    "service's log) under this dir")
    ap.add_argument("--results-dir", default=str(RESULTS))
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    if args.limit is not None and args.limit < 1:
        raise CheckError("--limit must be at least 1")
    if args.jobs < 1:
        raise CheckError("--jobs must be at least 1")
    bad = sorted(set(args.category or []) - set(CATEGORIES))
    if bad:
        raise CheckError(f"unknown category {bad}; the subset's categories "
                         f"are {sorted(CATEGORIES)}")
    if args.dataset_file:
        path = Path(args.dataset_file)
        if not path.is_file():
            raise CheckError(f"no dataset file at {path}")
        source = {"source": "local (unpinned)", "file": path.name,
                  "sha256": sha256(path), "pinned": False}
    else:
        path = fetch(args.dataset)
        ds = DATASETS[args.dataset]
        source = {"source": ds["source"], "file": ds["file"],
                  "sha256": ds["sha256"], "pinned": True}
    rows = load_rows(path)
    try:
        chosen, excluded = select(rows, args.category, None, args.ids)
    except (KeyError, TypeError) as exc:
        raise CheckError(f"{path} does not look like a CVDP dataset: {exc}") from exc
    in_subset = len(rows) - len(excluded)
    reasons: dict[str, int] = {}
    for why in excluded.values():
        reasons[why] = reasons.get(why, 0) + 1

    solutions = Path(args.solutions) if args.solutions else None
    if solutions is not None and not solutions.is_dir():
        raise CheckError(f"--solutions {solutions} is not a directory")
    if solutions is not None and args.borrow:
        raise CheckError("--solutions and --borrow score different things; "
                         "pick one")
    donor_ids: dict = {}
    if solutions is not None:
        mode = "solutions"
    elif args.borrow:
        mode = "borrowed"
        donor_ids = donors(rows, chosen)
        chosen = [r for r in chosen if r["id"] in donor_ids]
    elif chosen and all(has_solution(r) for r in chosen):
        mode = "reference"
    else:
        mode = "null"
    if args.limit is not None:
        chosen = chosen[:args.limit]

    facts = {"dataset": source, "mode": mode, "mode_note": MODE_NOTE[mode],
             "dataset_size": len(rows), "subset_size": in_subset,
             "excluded_by_reason": dict(sorted(reasons.items())),
             "selected": len(chosen), "limit": args.limit,
             "categories": args.category, "caveat": CAVEAT}
    if args.select_only:
        facts["ids"] = [r["id"] for r in chosen]
        if donor_ids:
            facts["donors"] = {r["id"]: donor_ids[r["id"]] for r in chosen}
        return checklib.report(SCRIPT, None, [], **facts), args.out
    if not chosen:
        raise CheckError("the selection is empty; widen --category or --limit")
    if not EDA.is_file():
        raise CheckError(f"no eda launcher at {EDA}")

    by_id = {r["id"]: r for r in rows}
    tries = {r["id"]: candidates(r, mode, solutions,
                                 [by_id[d] for d in donor_ids.get(r["id"], [])])
             for r in chosen}
    keep = Path(args.keep) if args.keep else None
    if keep:
        keep.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        records = list(pool.map(
            lambda r: run_problem(r, tries[r["id"]], args.timeout, keep), chosen))
    wall = round(time.monotonic() - t0, 1)
    if all(r["status"] == "error" for r in records):
        raise CheckError("no problem reached a verdict, so the native harness "
                         f"did not run at all (first: {records[0]['reason']}); "
                         "check `bin/eda python -m pytest --version` and "
                         "`bin/eda check-env`")

    violations = [checklib.violation(SCRIPT, "error", None, r["id"],
                                     "runner_error", [r["category"]],
                                     f"{r['id']}: {r['reason']}", "run.py")
                  for r in records if r["status"] == "error"]
    payload = checklib.report(SCRIPT, None, violations, **facts,
                              **aggregate(records), wall_s=wall,
                              jobs=args.jobs, timeout_s=args.timeout,
                              problems=records)
    stamp = dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    res_dir = Path(args.results_dir)
    res_dir.mkdir(parents=True, exist_ok=True)
    res = res_dir / f"{stamp}_{args.dataset}_{mode}.json"
    res.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    payload["result_file"] = str(res.relative_to(REPO)) \
        if res.resolve().is_relative_to(REPO) else str(res)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
