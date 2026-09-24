#!/usr/bin/env python
"""check_mutate.py - the mutate gate (docs/design.md 1.5, "### M2.").

    check_mutate.py --workspace DIR [--size N] [--seed N] [--jobs N] [--out FILE]

Drives real mcy (yosys's mutation-coverage tool, foss/tools/yosys/bin/mcy -
not through `bin/eda mcy`, which cannot resolve it, docs/design.md 1.2 and
check_env.py's own check_mcy for why; this launches it the same way
check_env.py does, `eda python3 <the real mcy script>`) over a scratch mcy
project under `<workspace>/log/mutate/` (recreated fresh every run - a stale
mutation database is worse than a slower gate). `[script]` reads rtl/ and
elaborates with a fixed `--top` from spec.yaml; `mutate -list <size> -seed
<seed>` (yosys's own pass, gates.yaml's "fixed seed, N mutants") samples the
design for candidate mutations; `[test sim]` (mutate_runner.py, one process
per mutant) yosys-applies each mutation to the frozen design and runs the
workspace's VISIBLE tb/ cocotb suite against it - PASS = survived, FAIL =
killed. A mutant mcy tags `-mode none` is its own built-in do-nothing
baseline (not a real mutation) and is excluded from every count here.

Kill-rate classification (this script's own scheme - gates.yaml names three
must-kill CLASSES but not a mechanism; nothing in mcy exposes a mutation's
own parameters to its `[logic]` section, so classification happens here,
after the run, straight off yosys's own `mutate -list` output strings):
  -mode inv                                  -> condition_inverted
  -mode const0/const1, -wire == "rst"        -> reset_removed  (a fixed,
                                                 project-wide convention: the
                                                 reset port is named 'rst')
  -mode const0/const1, -wire in an output port (spec.yaml `ports`, dir:
                                                 output)         -> output_stuck
  -mode const0/const1, otherwise             -> stuck_other
  -mode cnot0/cnot1                          -> conditional_stuck
  anything else                              -> other

Pass criteria (gates.yaml `mutate` row): kill rate >= 0.9, and no survivor in
{reset_removed, output_stuck, condition_inverted} (severity "error"; a
survivor outside those classes is reported at severity "info" - visible, not
failing). Fault this gate must catch: "a testbench that asserts nothing" (a
tb/ that never fails, however hard the design is mutated, drives kill rate
to ~0 and every survivor class fires).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import cocotblib  # noqa: E402
import speclib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_mutate"
EDA_BIN = REPO / "bin" / "eda"
MCY_REL = "foss/tools/yosys/bin/mcy"  # check_env.py's own MCY_REL, mirrored
MUTATE_RUNNER = SCRIPTS / "mutate_runner.py"
MCY_SUBDIR = "log/mutate"
DEFAULT_SIZE = 20
DEFAULT_SEED = 1
KILL_RATE_MIN = 0.9
RESET_PORT = "rst"
MUST_KILL_CLASSES = {"reset_removed", "output_stuck", "condition_inverted"}
INIT_TIMEOUT_S = 60.0
RUN_TIMEOUT_S = 600.0

MODE_RE = re.compile(r"-mode (\S+)")
WIRE_RE = re.compile(r"-wire (\S+)")
SRC_RE = re.compile(r"-src (\S+)")


def toolchain_root(timeout: float = 30.0) -> Path:
    proc = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError("could not resolve the eda toolchain root: "
                         f"{(proc.stderr or proc.stdout).strip()}")
    return Path(proc.stdout.strip())


def collect_sources(ws: Path) -> list[Path]:
    rtl_dir = ws / "rtl"
    files = sorted(rtl_dir.glob("*.v")) + sorted(rtl_dir.glob("*.sv"))
    if not files:
        raise CheckError(f"no .v/.sv files under {rtl_dir}")
    return files


def write_config(mcy_dir: Path, rtl_files: list[Path], top: str, tb_dir: Path,
                 size: int, seed: int) -> None:
    reads = "\n".join(f"read_verilog -sv {f.resolve()}" for f in rtl_files)
    config = f"""\
[options]
size {size}
seed {seed}

[script]
{reads}
hierarchy -top {top}
proc

[logic]
if result("sim") == "FAIL":
    tag("KILLED")
else:
    tag("SURVIVED")

[test sim]
maxbatchsize 1
expect PASS FAIL
run python3 {MUTATE_RUNNER} --tb {tb_dir.resolve()} --top {top}

[report]
print(f"mutants={{tags()}} killed={{tags('KILLED')}} survived={{tags('SURVIVED')}}")
"""
    (mcy_dir / "config.mcy").write_text(config, encoding="utf-8")


def run_mcy(mcy_dir: Path, mcy_real: Path, nproc: int) -> None:
    def mcy(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(EDA_BIN), "python3", str(mcy_real), *args], cwd=str(mcy_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=INIT_TIMEOUT_S if args and args[0] == "init"
            else RUN_TIMEOUT_S)

    init = mcy("init")
    if init.returncode != 0:
        raise CheckError(f"mcy init failed: "
                         f"{(init.stderr or init.stdout)[-2000:]}")
    ran = mcy("run", "-j", str(max(1, nproc)))
    if ran.returncode != 0:
        raise CheckError(f"mcy run failed: "
                         f"{(ran.stderr or ran.stdout)[-2000:]}")


def classify(mutation: str, output_ports: set[str]) -> str | None:
    mode_m = MODE_RE.search(mutation)
    mode = mode_m.group(1) if mode_m else None
    if mode is None or mode == "none":
        return None  # mcy's own do-nothing baseline row - not a mutant
    wire_m = WIRE_RE.search(mutation)
    wire = wire_m.group(1) if wire_m else None
    if mode == "inv":
        return "condition_inverted"
    if mode in ("const0", "const1"):
        if wire == RESET_PORT:
            return "reset_removed"
        if wire in output_ports:
            return "output_stuck"
        return "stuck_other"
    if mode in ("cnot0", "cnot1"):
        return "conditional_stuck"
    return "other"


def read_mutants(db_path: Path, output_ports: set[str]) -> list[dict]:
    con = sqlite3.connect(str(db_path))
    try:
        mutants = []
        for mid, mutation in con.execute(
                "SELECT mutation_id, mutation FROM mutations"):
            cls = classify(mutation, output_ports)
            if cls is None:
                continue
            tags = {t for (t,) in con.execute(
                "SELECT tag FROM tags WHERE mutation_id = ?", [mid])}
            src_m = SRC_RE.search(mutation)
            mutants.append({
                "id": mid, "mutation": mutation, "class": cls,
                "killed": "KILLED" in tags, "survived": "SURVIVED" in tags,
                "src": src_m.group(1) if src_m else None,
            })
        return mutants
    finally:
        con.close()


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--size", type=int, help="override the mutant count "
                    f"(default {DEFAULT_SIZE}, or spec.yaml mutate.size)")
    ap.add_argument("--seed", type=int, help="override the RNG seed "
                    f"(default {DEFAULT_SEED}, or spec.yaml mutate.seed)")
    ap.add_argument("--jobs", type=int, help="parallel mutant runs "
                    "(default: cpu count)")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = spec.get("top")
    if not isinstance(top, str) or not top.strip():
        raise CheckError("spec.yaml has no non-empty 'top' - run spec_lint "
                         "first")
    rtl_files = collect_sources(ws)
    tb_dir = ws / "tb"
    if not cocotblib.test_modules(tb_dir):
        raise CheckError(f"no test_*.py modules under {tb_dir}")

    mutate_cfg = spec.get("mutate") or {}
    size = args.size or mutate_cfg.get("size") or DEFAULT_SIZE
    seed = (args.seed if args.seed is not None
           else mutate_cfg.get("seed", DEFAULT_SEED))
    nproc = args.jobs or os.cpu_count() or 1
    output_ports = {name for name, p in (spec.get("ports") or {}).items()
                   if isinstance(p, dict) and p.get("dir") == "output"}

    t_root = toolchain_root()
    mcy_real = t_root / MCY_REL
    if not mcy_real.is_file():
        raise CheckError(f"mcy not found at {mcy_real}")

    mcy_dir = ws / MCY_SUBDIR
    shutil.rmtree(mcy_dir, ignore_errors=True)
    mcy_dir.mkdir(parents=True)
    write_config(mcy_dir, rtl_files, top, tb_dir, size, seed)

    t0 = time.monotonic()
    run_mcy(mcy_dir, mcy_real, nproc)
    wall_s = time.monotonic() - t0

    mutants = read_mutants(mcy_dir / "database" / "db.sqlite3", output_ports)
    if not mutants:
        raise CheckError("mcy produced no mutants to score (check spec.yaml "
                         "mutate.size and the design's own size)")
    unresolved = [m for m in mutants if not (m["killed"] or m["survived"])]
    if unresolved:
        raise CheckError(f"{len(unresolved)} of {len(mutants)} mutant(s) "
                         "never got a KILLED/SURVIVED tag - mcy run did not "
                         "finish cleanly")

    total = len(mutants)
    killed = sum(1 for m in mutants if m["killed"])
    survived = total - killed
    kill_rate = killed / total if total else 0.0

    survivors_by_class: dict[str, list[dict]] = {}
    for m in mutants:
        if m["survived"]:
            survivors_by_class.setdefault(m["class"], []).append(m)

    violations = []
    for cls, ms in sorted(survivors_by_class.items()):
        sev = "error" if cls in MUST_KILL_CLASSES else "info"
        for m in ms:
            violations.append(checklib.violation(
                "mutate", sev, m["src"], None, f"survivor_{cls}", [],
                f"mutant {m['id']} ({cls}) survived: {m['mutation']}",
                "mcy"))
    if kill_rate < KILL_RATE_MIN:
        violations.append(checklib.violation(
            "mutate", "error", None, None, "kill_rate_below_threshold", [],
            f"kill rate {kill_rate:.2f} ({killed}/{total}) is below "
            f"{KILL_RATE_MIN:.2f}", "mcy"))

    payload = checklib.report(
        SCRIPT, ws, violations, top=top, total_mutants=total, killed=killed,
        survived=survived, kill_rate=round(kill_rate, 4),
        survivors_by_class={c: len(v) for c, v in
                            sorted(survivors_by_class.items())},
        wall_s=round(wall_s, 2))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
