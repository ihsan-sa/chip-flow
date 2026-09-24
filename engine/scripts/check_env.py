#!/usr/bin/env python
"""check_env.py - the toolchain report (docs/design.md 1.2, "### M0.").

    check_env.py [--toolchain PATH] [--timeout SECONDS] [--out FILE]

Not a gate (no workspace, no violations list): a plain diagnostic, wired to
`eda check-env`, that answers one question - is the unpacked image usable,
right now, from this host - the way M0's done line asks: every tool's path
and version, the PDK version from ciel, and a smoke per row that proves the
tool actually launched rather than merely that a file exists at its path.
Every row runs through `eda` itself (bin/eda, this script's own sibling)
except the two that don't go through eda's dispatch table at all: the PDK
version, read straight off the ciel `current` file (see check_pdk), and
mcy, whose real script bin/eda's generic fallback cannot resolve (see
check_mcy) - both explain why in their own docstring rather than silently
special-casing.

A tool row that fails does not stop the others - the report always lists
every tool asked about, which is the point of a report meant to be read.

CLI (checklib's contract): JSON to stdout or --out, no prompts, ASCII.
Exit 0 every row ok, 1 some row failed (a report, not a crash), 2 the
toolchain tree itself could not be resolved (operational error).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import checklib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_env"
EDA_BIN = REPO / "bin" / "eda"
DEFAULT_TIMEOUT = 30.0

# name -> (eda args, a regex whose first match is the version string).
# Every one of these actually launches the tool through `eda` - the same
# loader-wrapping path a real gate uses - rather than just statting a path,
# so a row proves the tool runs on this host, not only that it exists.
CLI_TOOLS: dict[str, tuple[list[str], re.Pattern]] = {
    "yosys": (["yosys", "--version"], re.compile(r"Yosys \S+")),
    "sby": (["sby", "--version"], re.compile(r"SBY \S+")),
    "verilator": (["verilator", "--version"], re.compile(r"Verilator \S+")),
    "iverilog": (["iverilog", "-V"],
                 re.compile(r"Icarus Verilog version \S+")),
    "vvp": (["vvp", "-V"],
            re.compile(r"Icarus Verilog runtime version \S+")),
    "ngspice": (["ngspice", "--version"], re.compile(r"ngspice-\S+")),
    "openroad": (["openroad", "-version"], re.compile(r"\S+")),
    "sta": (["sta", "-version"], re.compile(r"\S+")),
    "klayout": (["klayout", "-v"], re.compile(r"KLayout \S+")),
    # any -batch run prints this banner before touching a single command,
    # so "quit" alone is the cheapest invocation that still proves netgen
    # itself launched under the loader.
    "netgen": (["netgen", "-batch", "quit"], re.compile(r"Netgen \S+")),
}

# magic has no --version flag; its own version is the reply to the "version"
# TCL command, fed on stdin like every other magic invocation in this repo
# (bin/eda's magic case always drops into the batch TCL interpreter, never
# takes a plain CLI flag).
MAGIC_ARGS = ["magic", "-noconsole"]
MAGIC_STDIN = "version\nquit -noprompt\n"
MAGIC_RE = re.compile(r"Version \S+ revision \S+")

# librelane and cocotb: `eda python -c "import X; print(X.__version__)"`,
# the same interpreter the flow's own scripts run under - this proves the
# import path check_env's own callers rely on, not just a binary somewhere
# in the tree.
PY_VERSION_TOOLS = {"librelane": "librelane", "cocotb": "cocotb"}

# The combined python-import smoke M0's done line names outright:
# `eda python -c "import cocotb, librelane, klayout, gdsfactory"`. Kept as
# one row, not four, because that is the exact form M0 tests - a package
# that imports alone but not alongside the other three (a namespace or
# dependency clash) is exactly the failure this row exists to catch.
PY_IMPORT_SMOKE = ["cocotb", "librelane", "klayout", "gdsfactory"]

# mcy ships as foss/tools/yosys/bin/mcy, and foss/tools/bin/mcy is one of
# the image's own absolute symlinks (-> /foss/tools/yosys/bin/mcy) meant to
# resolve inside a real container. bin/eda's generic fallback re-roots that
# style of symlink at $T (see its case, "a symlink inside the tree
# resolving to /foss/..."), but only when EVERY path component up to the
# link resolves - here the very first component, /foss, does not exist on
# this host at all, so `readlink -f` fails closed and `eda mcy` cannot find
# it. Rather than teach the fallback a second re-rooting rule for a tool
# nothing else in this repo dispatches by name, this checks mcy at its real,
# already-known path directly. mcy is a `#!/usr/bin/env python3` click app
# with no --version of its own (bundled with, and versioned by, this yosys
# build) - `--help` is the smoke, and its own banner line is the "version".
MCY_REL = "foss/tools/yosys/bin/mcy"

PDK_CURRENT_REL = "foss/pdks/ciel/gf180mcu/current"
PDK_LINK_REL = "foss/pdks/gf180mcuD"


def _env(toolchain: str | None) -> dict:
    env = dict(os.environ)
    if toolchain:
        env["EDA_TOOLCHAIN"] = toolchain
    return env


def _run_eda(args: list[str], timeout: float, toolchain: str | None,
            stdin_text: str | None = None):
    try:
        proc = subprocess.run(
            [str(EDA_BIN), *args], capture_output=True, text=True,
            timeout=timeout, input=stdin_text if stdin_text is not None else "",
            env=_env(toolchain))
        return proc, None
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:g}s"
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _first_line(text: str, fallback: str) -> str:
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip()
    return fallback


def resolve_toolchain_root(timeout: float, toolchain: str | None) -> Path:
    """Ask bin/eda for the toolchain root it would actually use - the ONE
    place `$HOME/.cc/toolchains/...` is the default (bin/eda's own `T=`
    line); nothing else in this repo repeats that literal."""
    proc, err = _run_eda(["--print-toolchain-root"], timeout, toolchain)
    if err or proc is None:
        raise CheckError(f"could not resolve the toolchain root: {err}")
    if proc.returncode != 0:
        raise CheckError(
            f"eda could not resolve a toolchain root: "
            f"{_first_line(proc.stderr or proc.stdout, 'no detail')}")
    root = proc.stdout.strip()
    if not root:
        raise CheckError("eda --print-toolchain-root printed nothing")
    return Path(root)


def check_cli_tool(name: str, args: list[str], pattern: re.Pattern,
                   timeout: float, toolchain: str | None) -> dict:
    proc, err = _run_eda(args, timeout, toolchain)
    if err:
        return {"tool": name, "ok": False, "version": None, "detail": err}
    out = (proc.stdout or "") + (proc.stderr or "")
    m = pattern.search(out)
    version = m.group(0) if m else None
    detail = version or _first_line(out, f"exit {proc.returncode}, no output")
    return {"tool": name, "ok": version is not None, "version": version,
            "detail": detail}


def check_magic(timeout: float, toolchain: str | None) -> dict:
    proc, err = _run_eda(MAGIC_ARGS, timeout, toolchain,
                         stdin_text=MAGIC_STDIN)
    if err:
        return {"tool": "magic", "ok": False, "version": None, "detail": err}
    out = (proc.stdout or "") + (proc.stderr or "")
    m = MAGIC_RE.search(out)
    version = m.group(0) if m else None
    detail = version or _first_line(out, f"exit {proc.returncode}, no output")
    return {"tool": "magic", "ok": version is not None, "version": version,
            "detail": detail}


def check_py_version(name: str, module: str, timeout: float,
                     toolchain: str | None) -> dict:
    code = f"import {module}; print(getattr({module}, '__version__', ''))"
    proc, err = _run_eda(["python3", "-c", code], timeout, toolchain)
    if err:
        return {"tool": name, "ok": False, "version": None, "detail": err}
    out = (proc.stdout or "").strip()
    ok = proc.returncode == 0 and bool(out)
    detail = out if ok else _first_line(proc.stderr or proc.stdout,
                                        f"exit {proc.returncode}, no output")
    return {"tool": name, "ok": ok, "version": out or None, "detail": detail}


def check_py_import_smoke(modules: list[str], timeout: float,
                          toolchain: str | None) -> dict:
    code = "import " + ", ".join(modules)
    proc, err = _run_eda(["python3", "-c", code], timeout, toolchain)
    name = "python-imports"
    if err:
        return {"tool": name, "ok": False, "version": None, "detail": err}
    ok = proc.returncode == 0
    detail = (f"import {', '.join(modules)}" if ok else
             _first_line(proc.stderr or proc.stdout,
                        f"exit {proc.returncode}, no output"))
    return {"tool": name, "ok": ok, "version": None, "detail": detail}


def check_mcy(t_root: Path, timeout: float, toolchain: str | None) -> dict:
    real = t_root / MCY_REL
    if not real.is_file():
        return {"tool": "mcy", "ok": False, "version": None,
                "detail": f"not found at {real}"}
    proc, err = _run_eda(["python3", str(real), "--help"], timeout, toolchain)
    if err:
        return {"tool": "mcy", "ok": False, "version": None, "detail": err}
    out = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0 and "Mutation Cover" in out
    detail = ("present, no --version of its own (bundled with this yosys "
              "build)") if ok else _first_line(out, f"exit {proc.returncode}")
    return {"tool": "mcy", "ok": ok, "version": None, "detail": detail}


def check_pdk(t_root: Path) -> dict:
    current = t_root / PDK_CURRENT_REL
    link = t_root / PDK_LINK_REL
    if not current.is_file():
        return {"tool": "pdk-gf180mcuD", "ok": False, "version": None,
                "detail": f"no {current}"}
    version = current.read_text(encoding="utf-8").strip()
    ok = bool(version) and link.is_dir()
    detail = (f"gf180mcuD -> ciel commit {version}" if ok else
             f"{link} missing or {current} is empty")
    return {"tool": "pdk-gf180mcuD", "ok": ok, "version": version or None,
            "detail": detail}


def build_report(toolchain: str | None, timeout: float) -> dict:
    t_root = resolve_toolchain_root(timeout, toolchain)
    rows = []
    for name, (args, pattern) in CLI_TOOLS.items():
        rows.append(check_cli_tool(name, args, pattern, timeout, toolchain))
    rows.append(check_magic(timeout, toolchain))
    rows.append(check_mcy(t_root, timeout, toolchain))
    for name, module in PY_VERSION_TOOLS.items():
        rows.append(check_py_version(name, module, timeout, toolchain))
    rows.append(check_pdk(t_root))
    rows.append(check_py_import_smoke(PY_IMPORT_SMOKE, timeout, toolchain))
    failing = [r["tool"] for r in rows if not r["ok"]]
    return {
        "script": SCRIPT,
        "toolchain": str(t_root),
        "status": "pass" if not failing else "fail",
        "tools": rows,
        "failing": failing,
    }


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--toolchain", help="override EDA_TOOLCHAIN")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="per-tool subprocess timeout, seconds")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)
    payload = build_report(args.toolchain, args.timeout)
    return payload, args.out


def main(argv=None) -> int:
    checklib.utf8_stdout()
    try:
        payload, out = run(argv)
    except CheckError as exc:
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": str(exc), "remediation": str(exc)}))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
