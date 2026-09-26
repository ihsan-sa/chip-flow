#!/usr/bin/env python
"""check_mutate.py - the mutate gate (docs/design.md 1.5, "### M2.").

    check_mutate.py --workspace DIR [--size N] [--seed N] [--jobs N]
                    [--pdr-timeout S] [--out FILE]

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
to ~0 and every survivor class fires) - and, just as fatal in the other
direction, "a testbench that doesn't even run" (an import error, a broken
fixture): every mutant crashes the same way the unmutated design does, mcy's
own `[logic]` counts every crash as a kill, and kill rate reads a false 1.0.
check_baseline() below catches that one, off mcy's own `-none` baseline row
(mutation id 1) rather than off any real mutant.

Equivalent mutants: a survivor no test could ever kill - because the
mutation does not change what the design does - is not a testbench gap.
Equivalent here means: started from reset (every FF at zero, or its
declared init), the mutant and the unmutated design give identical PRIMARY
OUTPUTS on every cycle for every input sequence; internal signals may
differ. After mcy runs, each SURVIVED mutant (in parallel) gets a proof over
a proof (prove_equivalent()): first yosys equiv_induct over every
name-matched signal (zero-init base case; all signals agreeing implies the
outputs do), then, over a miter of the two designs' outputs, k-induction in
yosys (a zero-init base case, then `sat -tempinduct`, the whole yosys
run bounded by EQUIV_TIMEOUT_S), and, only if the base case held but the induction step
did not, an unbounded model check over reachable states (sby `abc pdr`, up
to --pdr-timeout, default PDR_TIMEOUT_S = 30 minutes per mutant). The proof
also assumes a single clock straight from an input and asynchronous resets
acting at clock granularity. When the whole top is outside that clock
model (two clock domains, say) the same proof runs once more on the mutated
module alone - the `-module` mcy names, reference against mutant at that
module's own ports - and a PASS there stands for the top, since a module
whose outputs never differ cannot change anything around it; the reason
then ends "(module <m> alone, at its own ports)". A mutant in the top
module itself gets no such retry. A design or mutant outside the model, a
counterexample, a timeout or a tool error all leave the mutant a normal
survivor. Only a complete proof moves it - or the owner's ruling on that
one mutant: a survivor listed by id under `equivalent` in the workspace's
spec/mutant_rulings.yaml (engine/lib/rulingslib.py, with who ruled and the
evidence) is accepted without being sent to the prover, recorded in
`equivalent_by` as "accepted by owner ruling: <ruling>". A ruling that names
a killed mutant or an id mcy did not generate is refused (exit 2), so a
stale ruling cannot sit silently, and `below_spread` (an analog-only ruling)
is refused here. Kill rate = killed / (total - equivalent), and each proven
or ruled mutant is reported at severity "info" as `equivalent_<class>` (a
must-kill class included - it cannot be killed), with facts `equivalent`
and `equivalent_ids`. If every mutant is equivalent there is nothing left
to score, and that is an error, not a pass.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
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
import rulingslib  # noqa: E402
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
EQUIV_SUBDIR = "equiv"
EQUIV_TIMEOUT_S = 60.0
EQUIV_DEPTH = 8
PDR_TIMEOUT_S = 1800.0
PROVEN = "proven equivalent by "  # + "signal induction", "induction" or "pdr"
# ...and, for a proof scoped to the mutated module alone, + MODULE_SCOPE
RULED = "accepted by owner ruling: "  # + the entry's own `ruling`
# every FF type yosys's own `proc`/`opt` produce with a single CLK edge
# (docs: yosys "Flip-flop cells"); anything else sequential (latches, $sr,
# global-clock $ff, unmapped memories) is outside the proof's clock model.
CLOCKED_FFS = {"$dff", "$dffe", "$adff", "$adffe", "$sdff", "$sdffe",
               "$sdffce", "$aldff", "$aldffe", "$dffsr", "$dffsre"}
SEQ_HINTS = ("dff", "latch", "$sr", "$ff", "$mem", "$fsm")

MODE_RE = re.compile(r"-mode (\S+)")
WIRE_RE = re.compile(r"-wire (\S+)")
SRC_RE = re.compile(r"-src (\S+)")
MODULE_RE = re.compile(r"-module (\S+)")
TOP_IL_RE = re.compile(r"^attribute \\top 1\nmodule (\S+)$", re.M)


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


def check_baseline(db_path: Path) -> None:
    """mcy's own `-none` do-nothing baseline is always mutation id 1 (the
    first line `mutate -list ... -none ...` writes to mutations.txt, read
    back in insertion order by mcy's own `init` - see mcy's own script,
    "Importing mutations."). classify()/read_mutants() both treat it as "not
    a mutant" and skip it (module docstring above), so nothing else in this
    script ever looks at its result.

    That baseline result is exactly what a testbench that never even
    imports (a syntax error, a bad fixture) breaks: mutate_runner.py's own
    `[test sim]` step crashes for EVERY mutation, baseline included, and
    mcy's own `[logic]` block (`result("sim")=="FAIL" -> tag("KILLED")`)
    treats every crash as a kill - including the unmutated design's. Kill
    rate over the REAL mutants alone then reads 1.0 (every one "killed",
    for a reason that has nothing to do with the design), and the gate
    passes a tb that tests nothing. This is the one place that baseline's
    own tag is read, so it must PASS (mcy's "SURVIVED": the unmutated
    design behaves, as expected, under the visible tests) before a kill
    rate computed from anything else is trusted at all."""
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT mutation FROM mutations WHERE mutation_id = 1").fetchone()
        if row is None:
            raise CheckError("mcy database has no mutation id 1 (expected "
                             "its own '-mode none' baseline row)")
        mode_m = MODE_RE.search(row[0])
        if not mode_m or mode_m.group(1) != "none":
            raise CheckError("mcy mutation id 1 is not the '-mode none' "
                             f"baseline mcy always inserts first (got: "
                             f"{row[0]!r}) - cannot tell whether the "
                             "unmutated design passes the visible tests")
        tags = {t for (t,) in con.execute(
            "SELECT tag FROM tags WHERE mutation_id = 1")}
        if "KILLED" in tags:
            raise CheckError("the unmutated design fails the visible tests")
        if "SURVIVED" not in tags:
            raise CheckError("mcy's own baseline (mutation id 1) never got "
                             "a KILLED/SURVIVED tag - mcy run did not "
                             "finish cleanly")
    finally:
        con.close()


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


EQUIV_SCRIPT = """\
read_rtlil {design}
hierarchy {top}
flatten
rename -top ref
hierarchy -top ref
memory_map
write_json ref.json
write_rtlil ref.il
async2sync
design -stash ref
read_rtlil {design}
hierarchy {top}
{mutation}
flatten
rename -top uut
hierarchy -top uut
memory_map
write_json uut.json
write_rtlil uut.il
async2sync
design -copy-from ref -as ref ref
miter -equiv -flatten -make_assert ref uut miter
hierarchy -top miter
select -assert-min 1 miter/t:$assert
sat -seq {depth} -prove-asserts -set-init-zero -timeout {timeout} -verify miter
log {base_held}
sat -tempinduct -maxsteps {depth} -prove-asserts -set-init-zero -timeout {timeout} -verify miter
"""
BASE_HELD = "CHECK_MUTATE_BASE_CASE_HELD"


def single_clock(netlist: dict) -> tuple | None:
    """(clock bit, polarity) if every sequential cell of the (single,
    flattened) module is an edge-triggered FF on the SAME top-level input
    bit and edge, else None. The proof's sat model steps every FF on one
    implicit global clock and never looks at a CLK net - so a mutant that
    inverts, gates or re-routes a clock, and a design with more than one
    clock domain, is outside what it can decide."""
    mods = netlist.get("modules") or {}
    if len(mods) != 1:
        return None
    mod = next(iter(mods.values()))
    inputs = set()
    for port in (mod.get("ports") or {}).values():
        if port.get("direction") == "input":
            inputs.update(b for b in port.get("bits", []) if isinstance(b, int))
    clocks = set()
    for cell in (mod.get("cells") or {}).values():
        ctype = cell.get("type", "")
        if ctype in CLOCKED_FFS:
            clk = cell.get("connections", {}).get("CLK") or []
            pol = str(cell.get("parameters", {}).get("CLK_POLARITY", ""))
            if len(clk) != 1 or clk[0] not in inputs:
                return None
            clocks.add((clk[0], pol.lstrip("0") or "0"))
        elif any(h in ctype for h in SEQ_HINTS):
            return None
    if len(clocks) > 1:
        return None
    return next(iter(clocks)) if clocks else ("no clock",)


PDR_SBY = """\
[options]
mode prove
multiclock off
timeout {timeout}

[engines]
abc pdr

[script]
read_rtlil ref.il
read_rtlil uut.il
miter -equiv -flatten -make_assert ref uut miter
hierarchy -top miter
async2sync
setundef -zero -init
select -assert-min 1 miter/t:$assert
prep -top miter

[files]
ref.il
uut.il
"""


CLOCK_REFUSED = "not a single, unmodified clock - outside the proof"


def clock_refusal(work: Path) -> str | None:
    """None if ref.json and uut.json (written by EQUIV_SCRIPT) are both
    inside single_clock()'s model on the same clock, else why not."""
    try:
        ref = single_clock(json.loads((work / "ref.json").read_text()))
        uut = single_clock(json.loads((work / "uut.json").read_text()))
    except (OSError, ValueError) as exc:
        return f"could not read the netlists back: {exc}"
    if ref is None or uut is None or ref != uut:
        return CLOCK_REFUSED
    return None


def prove_reachable(work: Path, timeout: float = PDR_TIMEOUT_S
                    ) -> tuple[bool, str]:
    """The stronger proof, over REACHABLE states only: a miter of the
    reference against the mutant (`miter -equiv -make_assert`: every
    output agrees, every cycle), both copies started in the same zero (or
    declared-init) state (`setundef -zero -init`), handed to an unbounded
    model checker (sby prove mode, `abc pdr`, `multiclock off`). PASS means
    no reachable state of the pair tells the two apart through an output.
    Same assumptions as prove_equivalent() - zero init, one clock (its
    caller has already applied single_clock() to both netlists), async
    resets at clock granularity (`async2sync`) - plus that an undefined
    (x) constant reads as zero in both copies. Needs the ref.il/uut.il
    EQUIV_SCRIPT wrote into `work`. Anything short of sby's own PASS - a
    counterexample, a timeout, UNKNOWN, an error - is (False, why)."""
    pdr = work / "pdr"
    shutil.rmtree(pdr, ignore_errors=True)
    pdr.mkdir(parents=True)
    for name in ("ref.il", "uut.il"):
        shutil.copyfile(work / name, pdr / name)
    # sby's own timeout ends it cleanly a little early; the process
    # timeout is the hard bound
    (pdr / "miter.sby").write_text(
        PDR_SBY.format(timeout=max(1, int(timeout) - 5)), encoding="utf-8")
    try:
        proc = subprocess.run(
            [str(EDA_BIN), "sby", "-f", "miter.sby"], cwd=str(pdr),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"pdr: timeout after {timeout:.0f}s"
    except OSError as exc:
        return False, f"pdr: sby did not start: {exc}"
    out = proc.stdout + proc.stderr
    done = re.findall(r"DONE \((\w+), rc=(\d+)\)", out)
    if proc.returncode == 0 and done and done[-1] == ("PASS", "0"):
        return True, PROVEN + "pdr"
    if done:
        return False, f"pdr: {done[-1][0]}"
    err = [ln for ln in out.splitlines() if "ERROR" in ln]
    return False, (f"pdr: {err[0].strip()}" if err
                   else f"pdr: sby exit {proc.returncode}")


# The first, cheapest route: every name-matched signal (outputs included)
# agrees from reset for N cycles, and equiv_induct shows agreement on all of
# them is inductive. That implies the outputs agree, so it is sound for
# output equivalence; it is only incomplete (an internal difference fails
# it), which the outputs-only miter below then covers.
SIGNAL_EQUIV_SCRIPT = EQUIV_SCRIPT.split("design -copy-from ref")[0] + """\
design -copy-from ref -as ref ref
equiv_make -make_assert ref uut eqa
equiv_make ref uut equiv
select -assert-min 1 eqa/t:$assert
select -assert-min 1 equiv/t:$equiv
sat -seq {depth} -prove-asserts -set-init-zero -timeout {timeout} -verify eqa
equiv_induct -seq {depth} equiv
equiv_status -assert equiv
"""


def prove_signal_induction(design_il: Path, mutation: str, work: Path,
                           timeout: float = EQUIV_TIMEOUT_S,
                           top: str = "-auto-top") -> bool:
    """True when SIGNAL_EQUIV_SCRIPT proves every matched signal equal from
    reset and the clock check holds; any failure, timeout or error is
    False, never a proof."""
    sig = work / "signal"
    sig.mkdir(parents=True, exist_ok=True)
    (sig / "equiv.ys").write_text(SIGNAL_EQUIV_SCRIPT.format(
        top=top, design=design_il.resolve(), mutation=mutation, depth=EQUIV_DEPTH,
        timeout=int(timeout), base_held=BASE_HELD), encoding="utf-8")
    try:
        proc = subprocess.run(
            [str(EDA_BIN), "yosys", "-q", "-l", "equiv.log", "-s", "equiv.ys"],
            cwd=str(sig), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0 and clock_refusal(sig) is None


MODULE_SCOPE = " (module {} alone, at its own ports)"


def design_top(design_il: Path) -> str | None:
    """The module design.il marks as its top (`attribute \\top 1`), without
    RTLIL's leading backslash; None when it marks none."""
    try:
        m = TOP_IL_RE.search(design_il.read_text(encoding="utf-8",
                                                 errors="replace"))
    except OSError:
        return None
    return m.group(1).lstrip("\\") if m else None


def prove_equivalent(design_il: Path, mutation: str, work: Path,
                     timeout: float = EQUIV_TIMEOUT_S,
                     pdr_timeout: float = PDR_TIMEOUT_S) -> tuple[bool, str]:
    """prove_whole() over the whole top first. When that is not a proof
    and the top is outside the one-clock model (clock_refusal() on the
    top's own netlists - a design with two clock domains, say), the same
    proof runs once more on the mutated module alone (`-module` in the mcy
    mutation string): the reference module against the mutated one, at
    that module's own ports, with that module as the top. mcy mutates the
    module's definition, so every instance of it carries the mutation; if
    every instance's outputs equal the reference's for every input
    sequence, the top around them cannot tell the difference either - so
    a PASS there is a proof for the top. The module's own proof keeps
    every assumption of the whole-top one (its own single clock included),
    and anything short of a PASS leaves the mutant a survivor, reported
    with both reasons. A mutant in the top module itself has no smaller
    scope and gets no second try."""
    proven, why = prove_whole(design_il, mutation, work, timeout,
                              pdr_timeout)
    if proven or clock_refusal(work) != CLOCK_REFUSED:
        return proven, why
    mod_m = MODULE_RE.search(mutation)
    module = mod_m.group(1).lstrip("\\") if mod_m else None
    if not module or module == design_top(design_il):
        return proven, why
    m_proven, m_why = prove_whole(design_il, mutation, work / "module",
                                  timeout, pdr_timeout,
                                  top=f"-top {module}")
    if m_proven:
        return True, m_why + MODULE_SCOPE.format(module)
    return False, f"{why}; module {module} alone: {m_why}"


def prove_whole(design_il: Path, mutation: str, work: Path,
                timeout: float = EQUIV_TIMEOUT_S,
                pdr_timeout: float = PDR_TIMEOUT_S,
                top: str = "-auto-top") -> tuple[bool, str]:
    """Try to PROVE one mutant equivalent to the unmutated design:
    started from reset (every FF at zero, or its declared init), the two
    give the same PRIMARY OUTPUTS on every cycle for every input sequence.
    Internal signals are free to differ - a mutant that changes a register
    no output ever reflects is still equivalent.

    First, prove_signal_induction(): every name-matched signal agrees from
    reset and that agreement is inductive (yosys equiv_induct). That
    implies the outputs agree; it only misses mutants that differ
    internally, which the routes below cover.

    The next two routes check one miter of the two flattened modules (`miter
    -equiv -flatten -make_assert`: an assert per cycle that every output
    agrees). First by k-induction in yosys: a base case (`sat -seq N
    -prove-asserts -set-init-zero`: from reset the outputs agree for N
    cycles) and then `sat -tempinduct -maxsteps N` (N agreeing cycles
    from ANY state imply the next one agrees). Together they are an
    unbounded proof under three stated assumptions: FFs start at zero (or
    their declared init), one clock (single_clock() refuses anything else,
    on both the reference and the mutant), and asynchronous resets act at
    clock granularity (`async2sync`).

    The induction step checks ARBITRARY states, so a mutant that differs
    only on (or only inside) states the design never reaches can fail it.
    Only then - never for a base-case counterexample (a real difference),
    a timeout, an error or a clock refusal - prove_reachable() gets one
    more try over reachable states (pdr_timeout).

    `top` is the `hierarchy` argument that picks the design under proof:
    the whole top by default, `-top <module>` for prove_equivalent()'s
    module-scoped retry.

    Returns (proven, reason); a proof's reason is PROVEN + the method
    ("signal induction", "induction" or "pdr"). Anything short of a full
    proof is (False,
    why), never a proof."""
    work.mkdir(parents=True, exist_ok=True)
    if prove_signal_induction(design_il, mutation, work, timeout, top):
        return True, PROVEN + "signal induction"
    (work / "equiv.ys").write_text(EQUIV_SCRIPT.format(
        top=top, design=design_il.resolve(), mutation=mutation, depth=EQUIV_DEPTH,
        timeout=int(timeout), base_held=BASE_HELD), encoding="utf-8")
    try:
        proc = subprocess.run(
            [str(EDA_BIN), "yosys", "-q", "-l", "equiv.log", "-s", "equiv.ys"],
            cwd=str(work), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout:.0f}s"
    except OSError as exc:
        return False, f"yosys did not start: {exc}"
    if proc.returncode != 0:
        log = work / "equiv.log"
        text = log.read_text(encoding="utf-8", errors="replace") \
            if log.is_file() else ""
        err = [ln for ln in (text or proc.stderr or proc.stdout).splitlines()
               if ln.startswith("ERROR")]
        if BASE_HELD not in text:
            # a base-case counterexample, a solver timeout or a yosys
            # error: nothing the reachable-state proof may override
            if err and "proof did fail" in err[0]:
                return False, "outputs differ from reset (base case)"
            return False, err[0] if err else f"yosys exit {proc.returncode}"
        # the base case held and only the induction step failed
        why = "induction step failed"
        refused = clock_refusal(work)
        if refused:
            return False, refused
        proven, pdr_why = prove_reachable(work, pdr_timeout)
        return proven, pdr_why if proven else f"{why}; {pdr_why}"
    refused = clock_refusal(work)
    if refused:
        return False, refused
    return True, PROVEN + "induction"


def prove_survivors(design_il: Path, mutants: list[dict], work: Path,
                    jobs: int, pdr_timeout: float = PDR_TIMEOUT_S
                    ) -> dict[int, str]:
    """{id: reason} for every SURVIVED mutant, proven or not; the proven
    ones' reason starts with PROVEN. Proofs are independent, so they run
    side by side."""
    todo = [m for m in mutants if m["survived"]]
    if not todo:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max(1, jobs)) as ex:
        futs = {m["id"]: ex.submit(prove_equivalent, design_il, m["mutation"],
                                   work / str(m["id"]),
                                   pdr_timeout=pdr_timeout) for m in todo}
        return {mid: f.result()[1] for mid, f in futs.items()}


def ruled_survivors(rulings: dict, mutants: list[dict]) -> dict[int, str]:
    """{id: ruling} for every `equivalent` ruling in spec/mutant_rulings.
    yaml, each checked against this run: it must name a mutant mcy
    generated and that SURVIVED, else CheckError - a stale ruling is
    refused, never silently kept."""
    by_id = {m["id"]: m for m in mutants}
    out: dict[int, str] = {}
    for mid, entry in sorted(rulings["equivalent"].items()):
        where = f"{rulingslib.RULINGS_REL} equivalent id {mid}"
        if mid not in by_id:
            raise CheckError(f"{where} is not a mutant this run generated "
                             f"(ids: {', '.join(map(str, sorted(by_id)))}) - "
                             "remove the stale ruling")
        if not by_id[mid]["survived"]:
            raise CheckError(f"{where}: that mutant is killed by the tests "
                             "- remove the stale ruling")
        out[mid] = entry["ruling"]
    return out


def score(mutants: list[dict], equivalent: set[int],
          ruled: dict[int, str] | None = None) -> tuple[dict, list]:
    """The kill-rate arithmetic and findings, kept free of any tool so it
    can be tested alone. A proven-equivalent mutant, or one in `ruled`
    ({id: ruling} - the owner accepted it as equivalent), leaves the
    denominator (no test could ever kill it) and is reported at "info",
    whatever its class."""
    ruled = ruled or {}
    equivalent = set(equivalent) | set(ruled)
    scored = [m for m in mutants if m["id"] not in equivalent]
    equiv_ms = [m for m in mutants if m["id"] in equivalent]
    if not scored:
        raise CheckError(f"all {len(mutants)} mutant(s) were proven "
                         "or ruled equivalent to the design - nothing left to score; "
                         "raise spec.yaml mutate.size or change mutate.seed")
    total = len(scored)
    killed = sum(1 for m in scored if m["killed"])
    kill_rate = killed / total

    survivors_by_class: dict[str, list[dict]] = {}
    for m in scored:
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
    for m in sorted(equiv_ms, key=lambda m: m["id"]):
        how = (f"accepted as equivalent by owner ruling ({ruled[m['id']]})"
               if m["id"] in ruled else "proven equivalent to the design")
        violations.append(checklib.violation(
            "mutate", "info", m["src"], None, f"equivalent_{m['class']}", [],
            f"mutant {m['id']} ({m['class']}) is {how} and left out of the "
            f"kill rate: {m['mutation']}",
            "mutant_rulings" if m["id"] in ruled else "yosys"))
    if kill_rate < KILL_RATE_MIN:
        violations.append(checklib.violation(
            "mutate", "error", None, None, "kill_rate_below_threshold", [],
            f"kill rate {kill_rate:.2f} ({killed}/{total}) is below "
            f"{KILL_RATE_MIN:.2f}", "mcy"))
    facts = {
        "total_mutants": len(mutants), "equivalent": len(equiv_ms),
        "equivalent_ids": sorted(m["id"] for m in equiv_ms),
        "scored_mutants": total, "killed": killed, "survived": total - killed,
        "kill_rate": round(kill_rate, 4),
        "survivors_by_class": {c: len(v) for c, v in
                               sorted(survivors_by_class.items())},
    }
    return facts, violations


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--size", type=int, help="override the mutant count "
                    f"(default {DEFAULT_SIZE}, or spec.yaml mutate.size)")
    ap.add_argument("--seed", type=int, help="override the RNG seed "
                    f"(default {DEFAULT_SEED}, or spec.yaml mutate.seed)")
    ap.add_argument("--jobs", type=int, help="parallel mutant runs "
                    "(default: cpu count)")
    ap.add_argument("--pdr-timeout", type=float, default=PDR_TIMEOUT_S,
                    help="seconds of pdr per survivor that induction could "
                    f"not settle (default {PDR_TIMEOUT_S:.0f})")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    return ap.parse_args(argv)


def run(argv=None):
    args = parse_args(argv)

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

    rulings = rulingslib.load(ws, "mutate")

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

    db_path = mcy_dir / "database" / "db.sqlite3"
    check_baseline(db_path)
    mutants = read_mutants(db_path, output_ports)
    if not mutants:
        raise CheckError("mcy produced no mutants to score (check spec.yaml "
                         "mutate.size and the design's own size)")
    unresolved = [m for m in mutants if not (m["killed"] or m["survived"])]
    if unresolved:
        raise CheckError(f"{len(unresolved)} of {len(mutants)} mutant(s) "
                         "never got a KILLED/SURVIVED tag - mcy run did not "
                         "finish cleanly")

    ruled = ruled_survivors(rulings, mutants)
    proofs = prove_survivors(mcy_dir / "database" / "design.il",
                             [m for m in mutants if m["id"] not in ruled],
                             mcy_dir / EQUIV_SUBDIR, nproc, args.pdr_timeout)
    proven_by = {mid: why[len(PROVEN):] for mid, why in proofs.items()
                 if why.startswith(PROVEN)}
    equivalent = set(proven_by)
    facts, violations = score(mutants, equivalent, ruled)
    equivalent_by = {**proven_by,
                     **{mid: RULED + r for mid, r in ruled.items()}}
    wall_s = time.monotonic() - t0

    # stamp() hashes exactly this path as input_digest; gate.py's own
    # record_gate cross-checks that against invalidation.yaml's gate_inputs
    # kinds[0] for this gate ("rtl" for mutate) - the whole workspace would
    # never match that and silently fail every real recording.
    payload = checklib.report(
        SCRIPT, ws / "rtl", violations, top=top, **facts,
        equivalent_by={str(k): v for k, v in sorted(equivalent_by.items())},
        unproven_survivors={str(k): v for k, v in sorted(proofs.items())
                            if k not in equivalent},
        wall_s=round(wall_s, 2))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
