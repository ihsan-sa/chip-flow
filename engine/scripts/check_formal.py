#!/usr/bin/env python
"""check_formal.py - the formal gate (docs/design.md 1.5, "### M3.").

    check_formal.py --workspace DIR [--out FILE]

Runs SymbiYosys (`sby`, through `bin/eda`) over `formal/*.sv` - a small
structural wrapper module that instantiates the DUT and adds `` `ifdef
FORMAL `` immediate assert/cover statements (yosys's own formal frontend has
no SVA `assert property (@(...) ...)` support - proved empirically; only
procedural, i.e. "immediate", `assert (...)`/`cover (...)` inside an
`always` block work) referencing the DUT's ports directly.

Frontend. yosys's native `read -formal` is unsound on two things, both
proved empirically: it silently drops a `bind` (a bound always-false
assert never reaches sby's model), and it leaves a hierarchical reference
(`dut.q`) as an implicitly declared, undriven wire that sby turns into a
free input - or drops the assert outright inside a named block. So before
sby runs, pick_frontend probes the design with both frontends (hierarchy,
proc, flatten, then count property cells) and switches every task to the
yosys-slang plugin (`read_slang`, which resolves both) when native reports a
dotted implicit identifier, formal/*.sv holds a `bind`, native's model
has fewer property cells than slang's, or native could not read the design
at all while slang could (a hierarchical reference inside an expression,
`dut.a - dut.b`, stops native with an AST_AUTOWIRE error before it logs the
implicit identifier). A probe counts as reaching its property count only
when its own `log` line is in the output on a line of its own - yosys
echoes the whole -p script first, marks included. Slang needed but missing
from the toolchain, or unable to read the design, is a refusal - never a
quiet fall back to native - and so is neither frontend reading it (the
refusal names both errors). Under slang a property's sby id is its hierarchical name
(`dut.u_chk.LABEL`, `blk.LABEL`); a `property:` label matches an exact id,
else the one id whose last dotted component it is (two is refused as
ambiguous). The report carries `frontend` and `frontend_why`.

Clocks. Without sby's `multiclock on`, every flop ticks on every solver
step whatever its clock does, so a design with more than one clock, a
negedge flop or an async reset/latch is modelled wrongly: two input clocks
tied by an `assume` (a prescaler, a divider) make the assumptions
unsatisfiable, and an engine that does not check that then passes every
assert vacuously. The same probe dumps every flop, latch and clocked
check of the flattened model (clocking) and needs_multiclock turns
`multiclock on` for every task when it finds two or more clock signals, a
negative-edge one, or an async reset/load/latch cell. spec.yaml's
`formal: {multiclock: true}` forces it on; `multiclock: false` on a design
that needs it is refused. Under multiclock a clock is a free input and a
solver step is one clock EDGE, not a cycle, so `depth`/`cover_depth` count
edges (about twice the cycles). The report carries `multiclock` and
`multiclock_why`.

spec.yaml names which wrapper module to prep (`formal: {top}`, default
`<top>_formal`), how deep to look, and which requirements that wrapper must
prove (`check: formal|both` requirements each carry a `property:` label
matching an assert's own Verilog statement label in formal/*.sv).

Depth comes from the design, never from this gate: `formal: {depth: N}` is
required. A MISSING one is a finding, formal_depth_missing (an error, so
the gate fails and nothing runs): the property-writer owns setting it, and
a refusal would give the fix loop no work order to route there (a spec
written before depth was required was stuck with no legal fix). A present
but non-positive or malformed one is still refused (formal_settings).
`depth` is the PROVE depth only - the k of smt's k-induction and pdr's run -
so it should be what the asserts need for induction to close, which is
usually small: k-induction proves unboundedly, it does not have to walk a
long window. A deep `depth` makes smt's basecase infeasible (each step
costs seconds, so a ~1100-step basecase never finishes within the gate).
The long reach - a measurement window, a full frame, a counter wrap - goes
in `formal: {cover_depth: M}` (optional, M >= N), which only the cover task
uses. A shallow `depth` is never a loophole: an assert whose induction does
not close at it is still reported bounded, never proven.
`formal: {timeout_s: T}` (optional, at most TIMEOUT_MAX_S) sets every sby
task's timeout, which otherwise scales with that task's depth.

Three sby TASKS, same model; smt and pdr at `depth`, cov at `cover_depth`:
  smt  mode prove, engine smtbmc yices  - k-induction: a PASSING basecase
       AND a passing induction step together are a full, unbounded proof.
       Per-property basecase/induction status comes from smtbmc's own
       stdout ("returned pass/FAIL for basecase/induction" - sby has no
       per-property breakdown of THIS, only of which property failed, so a
       property that fails do so together at the task level; see
       _parse_task_status).
  pdr  mode prove, engine abc pdr        - the second opinion (gates.yaml:
       "abc pdr as the second opinion"). PDR gives no per-property
       breakdown at all (proved empirically: its own XML collapses every
       property into one "default" testcase) - only a design-level
       PASS/FAIL, applied to every property as corroboration.
  cov  mode cover, engine smtbmc yices   - "every cover reached": a COVER
       testcase with no <failure> in THIS task's XML was reached; one that
       IS present but not reached carries a <failure> here (proved
       empirically: an intentionally-unreachable cover comes back
       DONE(FAIL) with the specific COVER testcase failing, not the
       ASSERT ones, which the cover task never evaluates - they're
       <skipped> there instead). An unreached cover is an error
       cover_not_reached naming the depth and telling the fixer to raise
       formal.cover_depth, never the prove depth.
       The same task also covers every ASSERT (`chformal -assert2cover`,
       which keeps the assert's own id): a cover that is not reached means
       no trace within `cover_depth` ever enables that assert - see
       vacuous below.

Per-property (ASSERT-kind) verdict, applied per `property:` label:
  proven   smt task's own testcase has no <failure> AND smt's basecase AND
           induction both report "pass" AND the pdr task's own DONE is PASS.
  bounded  smt's basecase reports "pass" (no counterexample within `depth`
           steps) but induction did not (an inconclusive/unproven induction
           step, not a counterexample - sby also puts a <failure> on the
           property whose induction step failed, with trace_induct.vcd,
           and that is still bounded, never failed). Recorded with `depth` - "the
           gate never reports it as proven" (docs/design.md section 2) -
           a severity "info" finding, visible but never counted toward the
           gate's fail_severities: bounded to the spec's own asked depth is
           this gate's OWN passing outcome, just never claimed as a proof.
  failed   this property's own testcase carries a <failure> in the smt
           task and smt's basecase did not pass, OR the pdr task's overall DONE is FAIL while smt did not
           already fail it (an engine disagreement - PDR is a sound method
           for a safety property, so a PDR counterexample the k-induction
           run did not also find is treated as a real failure, not
           dismissed).
  vacuous  smt/pdr passed it (proven or bounded), but the cov task never
           reached it as a cover: no trace within `cover_depth` enables the
           assert (an `assume` or a gate condition rules out every state it
           checks, or the assumptions contradict each other). An error
           vacuous_pass - a vacuous pass never reads as a pass.

Async self-reset loops. A tri-state PFD's classic `AND(up, dn)` async clear
(both flops cleared the instant they are both set) is correct in sim and in
holdout, but under `multiclock on` sby's own clk2fflogic turns it into a
combinational loop - the clear depends combinationally on the very flops it
clears - and the smt2 step refuses with "Found logic loop in module X"
before any property is even attempted. Rewriting the RTL with an `` ifdef
FORMAL `` to break that loop is not this gate's route (docs/design.md says
why): the loop is cut in the FORMAL MODEL ONLY, never in rtl/ or
formal/*.sv, and only when spec.yaml's `formal.async_reset_cuts` names the
exact instance. Each entry is `{signal, why}` - `signal` the flop's own Q
net, dotted the same way a hierarchical reference already is elsewhere in
this file (`dut.up`), `why` the reason a human wrote down (async_reset_cuts,
never inferred from the netlist). Before sby ever runs, build_cut_il preps,
flattens and `dffunmap`s the design once, then cut_async_reset_nets
text-edits the resulting RTLIL (a real net-level edit, inspectable in
`log/formal/cut.il`, never a synthesis directive). What it abstracts is
the loop's feedback and nothing else: inside each declared flop's ARST
cone, every declared flop's Q is replaced by that flop's PRE-RESET value -
the value clk2fflogic gives it before this step's async reset, built from
the previous step's Q, D and ARST (one `$ff` each) and the clock edge - and
only the cone cells on a path from a declared Q are copied. Every other
ARST input (an external `~rst_n`) still reaches ARST in the same step, so
a reset requirement is scored exactly as the RTL has it, and the clear
still lands in the very step the second flop sets, so "never both high"
holds as it does in zero-delay sim once the clear has settled. What the
model cannot show is the delta-cycle glitch itself (both flops briefly
both-set before the clear catches up) and the reset pulse's width - sim's
job, never formal's. The PRE value copies clk2fflogic's own $adff model
(yosys 0.69), so each cut also carries a self-check assert
(`<signal>$async_cut_matches_clk2fflogic`) that the flop's real Q is the
cut ARST applied to PRE from step 1 on; a counterexample to it, or sby
never reporting it, is refused (check_cut_model) - a yosys that models
the flop differently can never pass through a stale cut. Only a plain
async-reset flop (`$adff`, or `$adffe` once `dffunmap` has split off its
enable) is covered; a `signal` that resolves to another async cell
($dffsr, $dlatch, ...) or to nothing at all, an ARST cone that reads a
part-select or concatenation, passes through a cell this gate does not
copy, or reads no declared Q at all (the declaration cuts nothing), is
refused (async_reset_cut_signal), same as a loop nobody declared (see
below). The report carries `async_reset_cuts` (the applied entries) and,
when any were applied, `async_reset_cuts_note` saying the above, and each
applied cut is also an info finding (async_reset_cut_applied), so a pass
resting on a cut never reads as an unqualified pass.

Refuses (CheckError, never a pass) rather than reports a finding when: no
requirement in spec.yaml has check: formal|both (an empty property set -
docs/design.md section 2's own silent-failure concern, applied here: a
formal gate with nothing to prove is not vacuously clean, it never ran);
a `property:` label spec.yaml names is not found as an ASSERT testcase in
sby's own model (the id sby echoes back, not a text search - a rename, or
under slang an immediate assert outside a named block, leaves the id
missing); a label that matches two ids; formal.depth missing or not a
positive int (a missing one is the formal_depth_missing finding, not a
refusal), or cover_depth/timeout_s/multiclock malformed (see Depth above); `multiclock: false` on a design that needs it (see Clocks above);
slang needed but unavailable or
unable to read the design (see Frontend above); any sby task fails to reach a DONE line at all (a crashed
launcher, a solver missing, a syntax error before the model even builds);
formal.async_reset_cuts is malformed, names a signal that is not a plain
async-reset flop's Q, has an ARST cone the cut cannot rebuild, has a
self-check assert that fails or never ran (check_cut_model), or a task's own log shows "Found logic loop" in a
module and no cut was declared for it (async_reset_cut_signal /
async_reset_loop_undeclared - see Async self-reset loops above).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import speclib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_formal"
EDA_BIN = REPO / "bin" / "eda"
SBY_SUBDIR = "log/formal"
# No default depth: a depth nobody chose once let a cover that needed ~1100
# cycles come back "not reached to depth 20" beside asserts "bounded to
# depth 20". spec.yaml's formal.depth is required (formal_settings).
PROBE_TIMEOUT_S = 180.0      # the yosys frontend probe - depth-independent
TIMEOUT_BASE_S = 180.0       # sby: base + per-step * depth, unless
TIMEOUT_PER_STEP_S = 1.0     # formal.timeout_s says otherwise
TIMEOUT_MAX_S = 7200.0       # hard ceiling on one sby task either way

DONE_RE = re.compile(r"DONE \((PASS|FAIL|ERROR|UNKNOWN)\b")
TASK_STATUS_RE = re.compile(
    r"returned (pass|FAIL|UNKNOWN) for (basecase|induction)")
IMPLICIT_HIER_RE = re.compile(
    r"Identifier `\\?([^'`\s]*\.[^'`\s]*)' is implicitly declared")
BIND_RE = re.compile(r"(?m)^\s*bind\s+[A-Za-z_]")
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT_RE = re.compile(r"//[^\n]*")
SLANG_SO = "foss/tools/slang-yosys-plugin/slang.so"
PROBE_MARK = "check_formal: property cells"
PROPERTY_CELLS = "t:$check t:$assert t:$assume t:$cover t:$live t:$fair"
CLOCK_MARK = "check_formal: clocked cells"
# every storage cell proc can leave, plus clocked checks ($check's TRG)
CLOCKED_CELLS = "t:$*dff* t:$*dlatch* t:$sr t:$check t:$memrd* t:$memwr*"
# cells whose output changes off a clock edge - async2sync, which sby runs
# without multiclock, turns them into something they are not
ASYNC_CELLS = {"$adff", "$adffe", "$aldff", "$aldffe", "$dffsr", "$dffsre",
               "$dlatch", "$adlatch", "$dlatchsr", "$sr"}
CELL_RE = re.compile(r"(?m)^\s*cell (\$\S+) (\S+)$")
# the only async-reset cell shape cut_async_reset_nets knows how to cut - a
# plain CLK+ARST flop, nothing with a SET port or a latch; build_cut_il's
# `dffunmap` has already turned an $adffe into an $adff and a $mux
ASYNC_CUT_CELL_TYPES = ("$adff",)
# the id of each cut's own self-check assert (see cut_async_reset_nets)
ASYNC_CUT_CHECK_SUFFIX = "$async_cut_matches_clk2fflogic"
CUT_IL_NAME = "cut.il"
LOGIC_LOOP_RE = re.compile(r"(?i)found logic loop in module (\S+?)[:!]")
ASYNC_CUT_FIX = (" (spec.yaml formal.async_reset_cuts: [{signal, why}, ...] "
                 "- see check_formal.py's own docstring, 'Async self-reset "
                 "loops')")


def collect_sources(ws: Path, sub: str, exts=(".v", ".sv")) -> list[Path]:
    d = ws / sub
    files = sorted(f for ext in exts for f in d.glob(f"*{ext}"))
    if not files:
        raise CheckError(f"no {'/'.join('*' + e for e in exts)} files under {d}")
    return files


def formal_requirements(spec: dict) -> dict[str, str]:
    """{property_label: requirement_id} for every requirement whose `check`
    is formal|both. Each MUST carry a non-empty `property` (spec_lint's own
    job to enforce - engine/lib/speclib.py - but this gate does not trust
    that it ran first any more than check_sim.py trusts spec_lint ran
    before it: an id-less or property-less entry is refused here too)."""
    out: dict[str, str] = {}
    for req in spec.get("requirements") or []:
        if not isinstance(req, dict) or req.get("check") not in ("formal", "both"):
            continue
        rid = req.get("id")
        prop = req.get("property")
        if not isinstance(rid, str) or not rid.strip():
            raise CheckError("a check: formal|both requirement has no 'id'")
        if not isinstance(prop, str) or not prop.strip():
            raise CheckError(f"requirement {rid} has check: {req.get('check')} "
                             "but no 'property' label naming its assert in "
                             "formal/*.sv")
        out[prop] = rid
    return out


DEPTH_REMEDIATION = (
    "set spec.yaml `formal: {depth: N}` to the induction depth the asserts "
    "need (usually small - a deep depth makes smtbmc's k-induction "
    "infeasible), and put a long reach in `formal: {cover_depth: M}`, at "
    "least the cycle length of the longest sequence a cover needs (a "
    "measurement window, a full frame, a counter wrap) plus the cycles to "
    "get out of reset")


def _positive_int(value, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CheckError(f"spec.yaml formal.{key} is {value!r}, not a "
                         f"positive integer - {DEPTH_REMEDIATION}")
    return value


def depth_missing_violation(spec: dict) -> dict | None:
    """The formal_depth_missing finding when spec.yaml's `formal:` is a
    mapping (or absent) without a `depth`, else None. A finding, not a
    refusal: setting the depth is the property-writer's job, and only a
    finding reaches it as a work order. Everything else formal_settings
    rejects (a non-mapping `formal:`, a depth that is not a positive int)
    stays a refusal. Pure."""
    cfg = spec.get("formal")
    if cfg is None:
        cfg = {}
    if not isinstance(cfg, dict) or cfg.get("depth") is not None:
        return None
    return checklib.violation(
        "formal", "error", "spec/spec.yaml", None, "formal_depth_missing",
        [], "spec.yaml has no formal.depth - the formal gate never picks a "
        "depth for you, so nothing was proven; " + DEPTH_REMEDIATION,
        "check_formal")


def formal_settings(spec: dict) -> dict:
    """The depths and sby timeouts this run uses, all from spec.yaml's own
    `formal:` key - never a default depth nobody chose. Pure, so the rules
    are testable without sby.

      depth        required, positive int: the prove depth (smt, pdr) -
                   the induction depth the asserts need, kept small.
      cover_depth  optional, positive int >= depth (deeper is stricter,
                   never looser): the cover task's depth, so a long cover
                   sequence need not make every prove run that deep.
                   Defaults to depth.
      timeout_s    optional, positive number <= TIMEOUT_MAX_S: every sby
                   task's timeout. Default per task: TIMEOUT_BASE_S +
                   TIMEOUT_PER_STEP_S * that task's depth, capped at
                   TIMEOUT_MAX_S.

    Refuses (CheckError) a missing or malformed formal.depth; run() turns
    a missing one into the formal_depth_missing finding before this is
    called (depth_missing_violation)."""
    cfg = spec.get("formal")
    if cfg is None:
        cfg = {}
    if not isinstance(cfg, dict):
        raise CheckError(f"spec.yaml formal is {cfg!r}, not a mapping - "
                         + DEPTH_REMEDIATION)
    if cfg.get("depth") is None:
        raise CheckError("spec.yaml has no formal.depth - the formal gate "
                         "never picks a depth for you; " + DEPTH_REMEDIATION)
    depth = _positive_int(cfg["depth"], "depth")
    cover_depth = depth
    if cfg.get("cover_depth") is not None:
        cover_depth = _positive_int(cfg["cover_depth"], "cover_depth")
        if cover_depth < depth:
            raise CheckError(
                f"spec.yaml formal.cover_depth ({cover_depth}) is below "
                f"formal.depth ({depth}) - a cover depth may only be deeper "
                "than the prove depth, never shallower")

    def scaled(d: int) -> float:
        return min(TIMEOUT_MAX_S, TIMEOUT_BASE_S + TIMEOUT_PER_STEP_S * d)

    timeout = cfg.get("timeout_s")
    if timeout is not None:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not 0 < timeout <= TIMEOUT_MAX_S):
            raise CheckError(f"spec.yaml formal.timeout_s is {timeout!r} - "
                             "it must be a number of seconds above 0 and at "
                             f"most {TIMEOUT_MAX_S:g}")
        prove_timeout = cover_timeout = float(timeout)
    else:
        prove_timeout, cover_timeout = scaled(depth), scaled(cover_depth)
    return {"depth": depth, "cover_depth": cover_depth,
            "prove_timeout_s": prove_timeout,
            "cover_timeout_s": cover_timeout}


def async_reset_cuts(spec: dict) -> list[dict]:
    """[{signal, why}, ...] from spec.yaml formal.async_reset_cuts - the
    declared, per-instance route for a self-resetting async-reset loop (see
    this module's docstring, "Async self-reset loops"). `[]` when the key
    is absent: this abstraction is opt-in, never inferred. Each `signal`
    names the flop's own Q net, dotted the way a hierarchical reference
    already is elsewhere in this gate (`dut.up`); each `why` is the human
    reason it was declared - required and non-empty, so a cut can never be
    silent. CheckError on anything malformed, including a signal declared
    twice. Pure."""
    cfg = spec.get("formal") or {}
    cuts = cfg.get("async_reset_cuts")
    if cuts is None:
        return []
    if not isinstance(cuts, list):
        raise CheckError("spec.yaml formal.async_reset_cuts must be a list "
                         "of {signal, why} entries" + ASYNC_CUT_FIX)
    out: list[dict] = []
    seen: set[str] = set()
    for i, entry in enumerate(cuts):
        where = f"spec.yaml formal.async_reset_cuts[{i}]"
        if not isinstance(entry, dict):
            raise CheckError(f"{where} must be a mapping" + ASYNC_CUT_FIX)
        unknown = set(entry) - {"signal", "why"}
        if unknown:
            raise CheckError(f"{where}: unknown field(s) "
                             f"{', '.join(sorted(unknown))}" + ASYNC_CUT_FIX)
        signal, why = entry.get("signal"), entry.get("why")
        if not isinstance(signal, str) or not signal.strip():
            raise CheckError(f"{where}: 'signal' must be a non-empty "
                             "hierarchical net name - the async-reset "
                             "flop's own Q (e.g. `dut.up`)" + ASYNC_CUT_FIX)
        if not isinstance(why, str) or not why.strip():
            raise CheckError(f"{where}: 'why' must be a non-empty string - "
                             "the cut is declared per instance, never "
                             "inferred from the netlist" + ASYNC_CUT_FIX)
        if signal in seen:
            raise CheckError(f"{where}: signal {signal!r} is listed twice"
                             + ASYNC_CUT_FIX)
        seen.add(signal)
        out.append({"signal": signal, "why": why})
    return out


CELL_BLOCK_START_RE = re.compile(r"^(\s*)cell (\$\S+) (\S+)$")
WIRE_DECL_RE = re.compile(r"^\s*wire\b(.*?)\s(\S+)$")
CONST_SIG_RE = re.compile(r"^(-?\d+|\d+'[01xzm-]*)$")
# combinational cells whose one output is \Y and every other port an input:
# the only cells cut_async_reset_nets copies when it rebuilds an ARST cone
CUT_COMB_CELLS = {
    "$not", "$pos", "$neg", "$buf", "$and", "$or", "$xor", "$xnor",
    "$reduce_and", "$reduce_or", "$reduce_xor", "$reduce_xnor",
    "$reduce_bool", "$logic_not", "$logic_and", "$logic_or", "$mux",
    "$pmux", "$eq", "$ne", "$eqx", "$nex", "$lt", "$le", "$gt", "$ge",
    "$add", "$sub", "$shl", "$shr", "$sshl", "$sshr"}
# cells with a \Y that is a free solver value, not a function of any net -
# a leaf of the cone, never copied
CUT_LEAF_Y_CELLS = {"$anyseq", "$anyconst", "$allseq", "$allconst",
                    "$initstate"}


def _cut_refuse(why: str) -> CheckError:
    return CheckError(f"formal.async_reset_cuts: {why}" + ASYNC_CUT_FIX)


def cut_async_reset_nets(text: str, signals: list[str]) -> tuple[str, dict[str, str]]:
    """(edited RTLIL text, {signal: cut flop's cell type}) after
    text-editing one already-`prep -top`'d-and-`flatten`'d module's RTLIL
    (see build_cut_il) so no declared flop's own Q - nor any other declared
    Q - reaches a declared flop's ARST combinationally. Each declared
    `signal` gets a PRE-RESET copy (`<signal>$async_pre`, built below from
    `$ff`s of the previous step's Q, D and cut ARST plus the clock edge,
    the way clk2fflogic models the flop). Each declared flop's ARST cone -
    the combinational cells (CUT_COMB_CELLS) and module-level `connect`
    aliases behind its ARST net - is rebuilt as a copy (`<net>$async_cut`)
    reading that PRE copy wherever the original read a declared Q; only the
    cells on a path from a declared Q are copied, so every other ARST input
    (an external `~rst_n`) still reaches ARST in the same step, and the
    original cone still drives everything else it drove. Each cut also gets
    a `$assert` named `<signal>` + ASYNC_CUT_CHECK_SUFFIX that, from step 1
    on, the flop's Q is the cut ARST applied to PRE (check_cut_model reads
    it). A real net-level edit, never a synthesis directive.

    Refuses (CheckError) rather than guess: a `signal` that is no cell's
    whole Q, or a cell other than $adff (ASYNC_CUT_CELL_TYPES), or one
    whose CLK/D/polarity/reset value it cannot read; an
    ARST cone that reads a part-select or concatenation, passes through a
    cell outside CUT_COMB_CELLS, or loops combinationally on its own; and
    an ARST cone that reads no declared Q at all (the declaration cuts
    nothing, so it is not the loop it claims to be). Pure (no I/O, no
    yosys) so the edit is testable without sby."""
    lines = text.split("\n")
    n = len(lines)
    # per module (keyed by its `module` line): every cell block, each
    # wire's width, each module-level `connect` whose lhs is one whole
    # wire, and the wires something drives only in part
    cells: list[dict] = []
    all_wires: dict[int | None, dict[str, str]] = {}
    all_aliases: dict[int | None, dict[str, str]] = {}
    all_partial: dict[int | None, set[str]] = {}
    module_line = None
    i = 0
    while i < n:
        s = lines[i].strip()
        if s.startswith("module "):
            module_line = i
        m = CELL_BLOCK_START_RE.match(lines[i])
        if m:
            j = i + 1
            conns, conn_line, params = {}, {}, {}
            while lines[j].strip() != "end":
                parts = lines[j].strip().split(" ", 2)
                if parts[0] == "connect" and len(parts) == 3:
                    conns[parts[1]] = parts[2]
                    conn_line[parts[1]] = j
                elif parts[0] == "parameter" and len(parts) == 3:
                    params[parts[1]] = parts[2]
                j += 1
            cells.append({"type": m.group(2), "name": m.group(3),
                          "indent": m.group(1), "start": i, "end": j,
                          "module": module_line, "conns": conns,
                          "conn_line": conn_line, "params": params})
            i = j + 1
            continue
        if s.startswith("wire "):
            wm = WIRE_DECL_RE.match(lines[i])
            if wm:
                toks = wm.group(1).split()
                width = toks[toks.index("width") + 1] if "width" in toks else "1"
                all_wires.setdefault(module_line, {})[wm.group(2)] = width
        elif s.startswith("connect "):
            toks = s.split()[1:]
            if len(toks) == 2 and toks[0] != "{":
                all_aliases.setdefault(module_line, {})[toks[0]] = toks[1]
            else:   # lhs is the first sigspec: `{ ... }` or `\w [i:j]`
                end = (toks.index("}") + 1 if toks[0] == "{" else
                       2 if len(toks) > 1 and toks[1].startswith("[") else 1)
                all_partial.setdefault(module_line, set()).update(
                    t for t in toks[:end] if t[0] in "\\$")
        i += 1

    def is_wire(sig: str) -> bool:
        return " " not in sig and sig[0] in "\\$" and not sig.startswith("{")

    flops: dict[str, dict] = {}
    for signal in signals:
        q = f"\\{signal}"
        owner = next((c for c in cells if c["conns"].get("\\Q") == q), None)
        if owner is None:
            raise CheckError(
                f"formal.async_reset_cuts names {signal!r}, which is not any "
                "cell's own Q in the flattened formal model - check the "
                "flop's net name (dotted the way a hierarchical reference is "
                "elsewhere in this gate, e.g. `dut.up`) and that it still "
                "exists" + ASYNC_CUT_FIX)
        if owner["type"] not in ASYNC_CUT_CELL_TYPES:
            raise CheckError(
                f"formal.async_reset_cuts names {signal!r}, but it is a "
                f"{owner['type']} cell's Q, not a plain $adff - this route "
                "only cuts a plain async-reset flop" + ASYNC_CUT_FIX)
        if "\\ARST" not in owner["conns"] or "\\WIDTH" not in owner["params"]:
            raise _cut_refuse(
                f"{signal!r}'s {owner['type']} cell has no ARST/WIDTH this "
                "gate can read - not a plain async-reset flop")
        arst = owner["conns"]["\\ARST"]
        if not is_wire(arst):
            raise _cut_refuse(
                f"{signal!r}'s ARST is {arst!r}, not one whole net - this "
                "gate cannot cut a reset it cannot trace")
        if any(f["module"] != owner["module"] for f in flops.values()):
            raise _cut_refuse("the declared signals sit in different modules")
        flops[signal] = owner

    mod = next(iter(flops.values()))["module"]
    wires = all_wires.get(mod, {})
    aliases = all_aliases.get(mod, {})
    partial = all_partial.get(mod, set())
    drivers: dict[str, dict] = {}
    for c in cells:
        y = c["conns"].get("\\Y")
        if c["module"] != mod or y is None:
            continue
        if is_wire(y):
            drivers[y] = c
        else:
            partial.update(t for t in y.replace("{", " ").split()
                           if t[0] in "\\$")

    new_wires: list[str] = []
    new_cells: list[str] = []
    indent = next(iter(flops.values()))["indent"]

    def add_wire(name: str, width: str) -> str:
        if name in wires:
            raise _cut_refuse(f"{name} already exists in the formal model")
        wires[name] = width
        new_wires.append(f"{indent}wire width {width} {name}")
        return name

    def add_cell(ctype: str, name: str, params: dict, conns: dict) -> None:
        new_cells.append(f"{indent}cell {ctype} {name}")
        new_cells.extend(f"{indent}  parameter {k} {v}" for k, v in params.items())
        new_cells.extend(f"{indent}  connect {k} {v}" for k, v in conns.items())
        new_cells.append(f"{indent}end")

    def add_ff(name: str, d: str, width: str) -> str:
        q = add_wire(name, width)
        add_cell("$ff", f"{name}_ff", {"\\WIDTH": width},
                 {"\\D": d, "\\Q": q})
        return q

    def add_mux(name: str, width: str, a: str, b: str, s: str) -> str:
        y = add_wire(name, width)
        add_cell("$mux", f"{name}_mux", {"\\WIDTH": width},
                 {"\\A": a, "\\B": b, "\\S": s, "\\Y": y})
        return y

    # Each declared Q's pre-reset value, rebuilt the way clk2fflogic (sby's
    # multiclock prep, yosys 0.69) models an $adff:
    #   Q[k] = ARST[k] ? R : (ARST[k-1] ? R : (edge[k] ? D[k-1] : Q[k-1]))
    # PRE is everything right of the first `?`: it reads only the previous
    # step's Q, D and ARST (one `$ff` each) and the clock, never this step's
    # Q, so a cone that reads PRE in place of Q has no loop and the clear
    # still lands in the step the flop sets.
    q_copy: dict[str, str] = {}
    for signal, flop in flops.items():
        q, w = f"\\{signal}", flop["params"]["\\WIDTH"]
        clk_pol = flop["params"].get("\\CLK_POLARITY")
        arst_pol = flop["params"].get("\\ARST_POLARITY")
        rval = flop["params"].get("\\ARST_VALUE")
        if clk_pol not in ("1'1", "1'0") or arst_pol not in ("1'1", "1'0") \
                or rval is None or "\\CLK" not in flop["conns"] \
                or "\\D" not in flop["conns"]:
            raise _cut_refuse(
                f"{signal!r}'s {flop['type']} cell has no CLK/D/CLK_POLARITY/"
                "ARST_POLARITY/ARST_VALUE this gate can read")
        clk = flop["conns"]["\\CLK"]
        clk_p = add_ff(f"{q}$async_clk_past", clk, "1")
        edge = add_wire(f"{q}$async_edge", "1")
        add_cell("$eqx", f"{q}$async_edge_eqx",
                 {"\\A_SIGNED": "0", "\\B_SIGNED": "0", "\\A_WIDTH": "2",
                  "\\B_WIDTH": "2", "\\Y_WIDTH": "1"},
                 {"\\A": f"{{ {clk_p} {clk} }}",
                  "\\B": "2'01" if clk_pol == "1'1" else "2'10",
                  "\\Y": edge})
        d_p = add_ff(f"{q}$async_d_past", flop["conns"]["\\D"], w)
        q_p = add_ff(f"{q}$async_q_past", q, w)
        hold = add_mux(f"{q}$async_hold", w, q_p, d_p, edge)
        flop["arst_past"] = add_wire(f"{q}$async_arst_past", "1")
        flop["rval"], flop["arst_pol"], flop["hold"] = rval, arst_pol, hold
        q_copy[q] = add_wire(f"{q}$async_pre", w)

    memo: dict[str, str | None] = {}
    visiting: set[str] = set()

    def cut(net: str) -> str | None:
        """The net to read in place of `net` inside an ARST cone: the $ff
        copy for a declared Q, a rebuilt copy when `net` depends
        combinationally on one, None when it depends on none."""
        if net in q_copy:
            return q_copy[net]
        if net in memo:
            return memo[net]
        if net in visiting:
            raise _cut_refuse(f"the ARST cone loops combinationally through "
                              f"{net} without passing a declared Q")
        if net in partial:
            raise _cut_refuse(f"the ARST cone reads {net}, which is driven "
                              "in part (a part-select or concatenation) - "
                              "this gate only rebuilds whole nets")
        visiting.add(net)
        result = None
        if net in drivers:
            c = drivers[net]
            if c["type"] in CUT_COMB_CELLS:
                subst = {}
                for port, sig in c["conns"].items():
                    if port == "\\Y" or CONST_SIG_RE.match(sig):
                        continue
                    if not is_wire(sig):
                        raise _cut_refuse(
                            f"the ARST cone's {c['type']} cell {c['name']} "
                            f"reads {sig!r}, not one whole net")
                    got = cut(sig)
                    if got is not None:
                        subst[port] = got
                # brief: "leave every other ARST input (~rst_n)
                # combinational" - a cell with no input on a path from a
                # declared Q is never copied, so the cone keeps reading it
                if subst:
                    result = f"{net}$async_cut"
                    if result in wires:
                        raise _cut_refuse(f"{result} already exists")
                    subst["\\Y"] = result
                    new_wires.append(f"{indent}wire width {wires.get(net, '1')} {result}")
                    block = lines[c["start"]:c["end"] + 1]
                    block[0] = f"{c['indent']}cell {c['type']} {c['name']}$async_cut"
                    for port, sig in subst.items():
                        k = c["conn_line"][port] - c["start"]
                        pad = block[k][:len(block[k]) - len(block[k].lstrip())]
                        block[k] = f"{pad}connect {port} {sig}"
                    new_cells.extend(block)
            elif c["type"] not in CUT_LEAF_Y_CELLS:
                raise _cut_refuse(
                    f"the ARST cone passes through a {c['type']} cell "
                    f"({c['name']}), which this gate does not rebuild")
        elif net in aliases:
            src = aliases[net]
            if not CONST_SIG_RE.match(src):
                if not is_wire(src):
                    raise _cut_refuse(f"the ARST cone reads {net}, an alias "
                                      f"of {src!r}, not one whole net")
                result = cut(src)
        visiting.discard(net)
        memo[net] = result
        return result

    edits: dict[int, str] = {}
    applied = {}
    for signal, flop in flops.items():
        arst = flop["conns"]["\\ARST"]
        new_arst = cut(arst)
        if new_arst is None:
            raise _cut_refuse(
                f"{signal!r}'s ARST ({arst}) reads no declared signal's Q "
                "combinationally - the declared cut would change nothing, so "
                "it is not the self-reset loop it names")
        k = flop["conn_line"]["\\ARST"]
        pad = lines[k][:len(lines[k]) - len(lines[k].lstrip())]
        edits[k] = f"{pad}connect \\ARST {new_arst}"
        applied[signal] = flop["type"]
        q, w = f"\\{signal}", flop["params"]["\\WIDTH"]
        rval, hold = flop["rval"], flop["hold"]
        on, off = (rval, hold) if flop["arst_pol"] == "1'1" else (hold, rval)
        add_cell("$ff", f"{flop['arst_past']}_ff", {"\\WIDTH": "1"},
                 {"\\D": new_arst, "\\Q": flop["arst_past"]})
        add_cell("$mux", f"{q_copy[q]}_mux", {"\\WIDTH": w},
                 {"\\A": off, "\\B": on, "\\S": flop["arst_past"],
                  "\\Y": q_copy[q]})
        # the self-check: from step 1 on, the flop's own Q (clk2fflogic's
        # model) is the cut ARST applied to PRE - if a yosys change ever
        # models the flop differently, this assert fails and run() refuses
        pre = q_copy[q]
        a, b = (pre, rval) if flop["arst_pol"] == "1'1" else (rval, pre)
        expect = add_mux(f"{q}$async_expect", w, a, b, new_arst)
        same = add_wire(f"{q}$async_same", "1")
        add_cell("$eqx", f"{q}$async_same_eqx",
                 {"\\A_SIGNED": "0", "\\B_SIGNED": "0", "\\A_WIDTH": w,
                  "\\B_WIDTH": w, "\\Y_WIDTH": "1"},
                 {"\\A": q, "\\B": expect, "\\Y": same})
        init = add_wire(f"{q}$async_initstate", "1")
        add_cell("$initstate", f"{init}_cell", {}, {"\\Y": init})
        add_cell("$assert", f"{q}{ASYNC_CUT_CHECK_SUFFIX}", {},
                 {"\\A": same, "\\EN": f"{init}_n"})
        add_wire(f"{init}_n", "1")
        add_cell("$not", f"{init}_not",
                 {"\\A_SIGNED": "0", "\\A_WIDTH": "1", "\\Y_WIDTH": "1"},
                 {"\\A": init, "\\Y": f"{init}_n"})
    for k, ln in edits.items():
        lines[k] = ln
    end = next(k for k in range(mod + 1, n) if lines[k] == "end")
    out = (lines[:mod + 1] + new_wires + lines[mod + 1:end] + new_cells
           + lines[end:])
    return "\n".join(out), applied


def build_cut_il(sby_dir: Path, frontend: str, sv_files: list[Path],
                 rtl_files: list[Path], formal_top: str,
                 slang_so: Path | None, cuts: list[dict]) -> tuple[Path, list[dict]]:
    """Prep and flatten the design once (own yosys run, before sby ever
    sees it), apply every declared cut at once (cut_async_reset_nets: the
    cuts share one rebuilt ARST cone), and write the result to sby_dir/CUT_IL_NAME. Returns (that path,
    [{signal, why, cell_type}, ...] - the applied abstraction, for the gate
    JSON). `flatten` only runs on this path (never for a design with no
    declared cuts): cut_async_reset_nets needs the flop's Q as one flat net
    name, the same dotted convention a hierarchical reference already uses
    in this gate."""
    reads = yosys_reads(frontend, sv_files, rtl_files, formal_top, slang_so)
    raw_path = sby_dir / "precut.il"
    script = (reads.replace("\n", "; ")
              + f"; prep -top {formal_top}; flatten; dffunmap; "
              f"write_rtlil {raw_path}")
    try:
        proc = subprocess.run([str(EDA_BIN), "yosys", "-p", script],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"yosys (async_reset_cuts prep) timed out: {exc}") from exc
    if proc.returncode != 0 or not raw_path.is_file():
        raise CheckError(
            "yosys could not prep the design to apply formal."
            f"async_reset_cuts: {(proc.stdout + proc.stderr)[-2000:]}")
    text = raw_path.read_text(encoding="utf-8")
    text, ctypes = cut_async_reset_nets(text, [c["signal"] for c in cuts])
    applied = [{**cut, "cell_type": ctypes[cut["signal"]]} for cut in cuts]
    cut_path = sby_dir / CUT_IL_NAME
    cut_path.write_text(text, encoding="utf-8")
    return cut_path, applied


def yosys_reads(frontend: str, sv_files: list[Path], rtl_files: list[Path],
                formal_top: str, slang_so: Path | None) -> str:
    """The yosys commands that read the design for `frontend` - "native"
    (yosys's own `read -formal`, one file per line) or "slang" (the
    yosys-slang plugin, every file in one compilation unit so a `bind` in
    formal/*.sv can reach a DUT module in rtl/)."""
    files = [f.resolve() for f in (*sv_files, *rtl_files)]
    if frontend == "slang":
        return (f"plugin -i {slang_so}\n"
                f"read_slang -D FORMAL --top {formal_top} "
                + " ".join(str(f) for f in files))
    return "\n".join(f"read -formal -D FORMAL {f}" for f in files)


def write_sby(path: Path, sv_files: list[Path], rtl_files: list[Path],
             formal_top: str, mode: str, engine: str, depth: int,
             frontend: str = "native", slang_so: Path | None = None,
             multiclock: bool = False, vacuity: bool = False,
             cut_il: Path | None = None) -> None:
    """One task's .sby. `multiclock` turns sby's multiclock mode on (see
    Clocks above); `vacuity` turns every assert into a cover of itself
    (the cov task's non-vacuity check, see vacuous above). `cut_il`
    (build_cut_il's output) reads that already-prepped, already-cut RTLIL
    directly instead of the source files and skips `prep` (it was already
    prepped, and flattened, to make the cut) - the declared
    formal.async_reset_cuts route."""
    files = "\n".join(str(f.resolve()) for f in (*sv_files, *rtl_files))
    if cut_il is not None:
        reads = f"read_rtlil {cut_il.resolve()}"
        prep = "chformal -assert2cover" if vacuity else ""
    else:
        reads = yosys_reads(frontend, sv_files, rtl_files, formal_top, slang_so)
        prep = f"prep -top {formal_top}" + (
            "\nchformal -assert2cover" if vacuity else "")
    path.write_text(f"""\
[options]
mode {mode}
depth {depth}
multiclock {"on" if multiclock else "off"}

[engines]
{engine}

[script]
{reads}
{prep}

[files]
{files}
""", encoding="utf-8")


def strip_comments(text: str) -> str:
    return LINE_COMMENT_RE.sub("", BLOCK_COMMENT_RE.sub(" ", text))


def has_bind(sv_files: list[Path]) -> bool:
    """A `bind` statement anywhere in formal/*.sv (comments stripped). Only
    ever a trigger for the slang frontend, never a reason to trust the
    native one: a false positive costs a slang read, nothing else."""
    return any(BIND_RE.search(strip_comments(f.read_text(encoding="utf-8",
                                                         errors="replace")))
               for f in sv_files)


def hier_refs(native_log: str) -> list[str]:
    """Dotted identifiers yosys's native frontend declared implicitly - a
    hierarchical reference (`dut.q`) it cannot resolve, which sby then
    turns into a free input (`setundef -undriven -anyseq`)."""
    return sorted(set(IMPLICIT_HIER_RE.findall(native_log)))


def probe_error(log: str) -> str:
    """The line saying why a probe failed: slang's own `file:line: error:`
    first (its ERROR line only says elaboration failed), else yosys's
    first ERROR line, else a note that the log has neither."""
    m = (re.search(r"(?m)^.*: error: .*$", log)
         or re.search(r"(?m)^.*ERROR:.*$", log))
    return m.group(0).strip()[-300:] if m else "no error line in its log"


def after_mark(log: str, mark: str) -> str | None:
    """The probe log past the line its own `log <mark>` printed, or None
    when the probe never got there. Anchored to a whole line because yosys
    echoes the whole -p script first ("-- Running command `...log <mark>;
    ...`"), so a bare substring test passes even for a probe that died
    reading the design."""
    m = re.search(rf"(?m)^{re.escape(mark)}$", log)
    return log[m.end():] if m else None


def count_properties(log: str, formal_top: str) -> int | None:
    """Property cells `select -list` printed for the flattened top, or None
    when the probe never reached that command (a parse error)."""
    text = after_mark(log, PROBE_MARK)
    if text is None:
        return None
    return len(re.findall(rf"(?m)^{re.escape(formal_top)}/\S+$", text))


def probe(frontend: str, sv_files: list[Path], rtl_files: list[Path],
          formal_top: str, slang_so: Path | None) -> str:
    script = (yosys_reads(frontend, sv_files, rtl_files, formal_top, slang_so)
              .replace("\n", "; ")
              + f"; hierarchy -top {formal_top}; proc; flatten; "
              f"log {PROBE_MARK}; select -list {PROPERTY_CELLS}; "
              f"opt_clean; log {CLOCK_MARK}; dump {CLOCKED_CELLS}")
    try:
        proc = subprocess.run([str(EDA_BIN), "yosys", "-p", script],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"yosys ({frontend} probe) timed out: {exc}") from exc
    return (proc.stdout or "") + (proc.stderr or "")


def clocking(log: str) -> dict | None:
    """{"clocks": [signal, ...], "negedge": [cell, ...], "async": [cell
    type, ...]} over the probe's dump of every flop, latch and clocked
    check in the flattened model (opt_clean first, so one net has one
    name), or None when the probe never reached the dump. Pure."""
    text = after_mark(log, CLOCK_MARK)
    if text is None:
        return None
    clocks, negedge, asyncs = set(), [], set()
    starts = list(CELL_RE.finditer(text))
    for i, m in enumerate(starts):
        ctype, name = m.group(1), m.group(2)
        body = text[m.end():starts[i + 1].start() if i + 1 < len(starts)
                    else len(text)]
        if ctype in ASYNC_CELLS:
            asyncs.add(ctype)
        port, pol = (("TRG", "TRG_POLARITY") if ctype == "$check"
                     else ("CLK", "CLK_POLARITY"))
        conn = re.search(rf"(?m)^\s*connect \\{port} (.+)$", body)
        if ctype.startswith("$mem") and not re.search(
                r"(?m)^\s*parameter \\CLK_ENABLE \d+'[01x]*1", body):
            continue  # an asynchronous memory port has no clock
        if conn is None or conn.group(1).strip() in ("{ }", "1'x"):
            continue  # an unclocked check, or a cell with no clock port
        clocks.add(conn.group(1).strip())
        pm = re.search(rf"(?m)^\s*parameter \\{pol} \d+'([01x]+)$", body)
        if pm and "0" in pm.group(1):
            negedge.append(name)
    return {"clocks": sorted(clocks), "negedge": sorted(negedge),
            "async": sorted(asyncs)}


def needs_multiclock(clk: dict | None, spec: dict) -> tuple[bool, str | None]:
    """(multiclock on?, why). On when the design has two or more clock
    signals, a negative-edge one or an async/latch cell (clocking), or
    when spec.yaml says `formal: {multiclock: true}`. `multiclock: false`
    on a design that needs it is refused, never obeyed. Pure."""
    # brief: "multiclock when the design or spec needs it"
    want = (spec.get("formal") or {}).get("multiclock")
    if want is not None and not isinstance(want, bool):
        raise CheckError(f"spec.yaml formal.multiclock is {want!r} - it "
                         "must be true or false (or absent to let the gate "
                         "decide from the design's clocks)")
    if clk is None:
        raise CheckError("the yosys probe never reached its dump of clocked "
                         "cells, so the gate cannot tell whether the design "
                         "needs sby's multiclock mode")
    reasons = []
    if len(clk["clocks"]) > 1:
        reasons.append(f"{len(clk['clocks'])} clock signals "
                       f"({', '.join(clk['clocks'])})")
    if clk["negedge"]:
        reasons.append(f"negative-edge cell(s) {', '.join(clk['negedge'][:3])}")
    if clk["async"]:
        reasons.append(f"async reset/latch cell(s) {', '.join(clk['async'])}")
    why = "; ".join(reasons) or None
    if why and want is False:
        raise CheckError(
            f"spec.yaml formal.multiclock is false but the design has {why}"
            " - without multiclock every flop ticks on every solver step, "
            "which is not this design; remove formal.multiclock or set it "
            "true")
    if want and not why:
        return True, "spec.yaml formal.multiclock: true"
    return bool(why), why


def slang_plugin() -> Path | None:
    """yosys-slang's .so in the eda toolchain (share/yosys/plugins/slang.so
    is an absolute symlink into the image's /foss, dead outside it)."""
    try:
        root = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    so = Path(root.stdout.strip()) / SLANG_SO
    return so if root.returncode == 0 and so.is_file() else None


def pick_frontend(sv_files: list[Path], rtl_files: list[Path],
                  formal_top: str
                  ) -> tuple[str, str | None, Path | None, str]:
    """(frontend, why slang was needed or None, slang .so, that frontend's
    probe log - clocking reads it). Native unless
    it would be unsound: a hierarchical reference it leaves undriven, a
    `bind` it drops, fewer properties in its flattened model than
    slang's (anything else it dropped), or a design it could not read that
    slang could. Slang needed but missing or unable to read the design, or
    neither frontend reading it, is a refusal - never a quiet fall back to
    native."""
    native_log = probe("native", sv_files, rtl_files, formal_top, None)
    refs = hier_refs(native_log)
    bind = has_bind(sv_files)
    slang_so = slang_plugin()
    slang_log = (probe("slang", sv_files, rtl_files, formal_top, slang_so)
                 if slang_so else "")
    native_n = count_properties(native_log, formal_top)
    slang_n = count_properties(slang_log, formal_top)
    why = None
    if refs:
        why = f"hierarchical reference(s) {', '.join(refs)}"
    elif bind:
        why = "a `bind` statement in formal/*.sv"
    elif native_n is None and slang_n is not None:
        # a hierarchical reference inside an expression (`dut.a - dut.b`)
        # stops native with an AST_AUTOWIRE error before it ever logs the
        # implicit declaration hier_refs looks for
        why = ("yosys's native frontend could not read formal/*.sv ("
               + probe_error(native_log) + ")")
    elif native_n is not None and slang_n is not None and slang_n > native_n:
        why = (f"yosys's native frontend keeps {native_n} property cell(s) "
               f"where yosys-slang keeps {slang_n}")
    if why is None and native_n is None and slang_so is not None:
        raise CheckError(
            "neither yosys frontend could read formal/*.sv with rtl/ - "
            f"native: {probe_error(native_log)}; yosys-slang: "
            f"{probe_error(slang_log)}"
            + ("; slang reads no signal inside an `initial` block, so "
               "assume reset in an `always @*` on a past_valid flag instead"
               if "during design initialization" in slang_log else ""))
    if why is None:
        return "native", None, None, native_log
    if slang_so is None:
        raise CheckError(
            f"formal/*.sv uses {why}, which yosys's native frontend drops "
            "or leaves undriven, and the yosys-slang plugin that can read it "
            f"is not in the eda toolchain ({SLANG_SO}) - use a plain "
            "wrapper that reaches the DUT through its ports instead")
    if slang_n is None:
        raise CheckError(
            f"formal/*.sv uses {why}, so it must be read by yosys-slang, "
            f"and yosys-slang could not read it: {slang_log[-2000:]}")
    return "slang", why, slang_so, slang_log


def run_sby(sby_dir: Path, config: Path, workdir_name: str,
            timeout_s: float = TIMEOUT_BASE_S) -> tuple[str, Path]:
    """Run one sby task, -f'd to a fresh workdir under sby_dir. Returns
    (stdout+stderr text, the task's own workdir) - never raises on a
    property FAIL (that is a normal, parseable outcome via the workdir's
    XML/stdout), only on a launcher that never reached a DONE line at all.

    sby_dir/config/workdir are all resolved to absolute FIRST (M5, found by
    actually running this gate against a relative --workspace): passing
    `cwd=str(sby_dir)` while ALSO passing `config`/`workdir` as the same
    ws-relative path sby sees them nested a second time under its own
    (now-cwd) directory - `sby_dir/<that same relative path again>` - and
    fails to find them. Absolute throughout sidesteps this regardless of
    whether the caller's own ws was relative or absolute."""
    sby_dir = Path(sby_dir).resolve()
    config = Path(config).resolve()
    workdir = sby_dir / workdir_name
    try:
        proc = subprocess.run(
            [str(EDA_BIN), "sby", "-f", str(config), "-d", str(workdir)],
            cwd=str(sby_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"sby ({workdir_name}) timed out after "
                         f"{timeout_s:g}s - raise spec.yaml formal.timeout_s "
                         f"(at most {TIMEOUT_MAX_S:g}) or shorten the "
                         f"sequences the properties need: {exc}") from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    if not DONE_RE.search(output):
        raise CheckError(
            f"sby ({workdir_name}) exited {proc.returncode} with no DONE "
            f"line - the launcher likely never reached a real run: "
            f"{output[-2000:]}")
    return output, workdir


def done_status(output: str) -> str:
    m = DONE_RE.search(output)
    return m.group(1) if m else "ERROR"


def task_substatus(output: str) -> dict[str, str | None]:
    """{'basecase': 'pass'|'FAIL'|'UNKNOWN'|None, 'induction': ...} off
    smtbmc's own "Status returned by engine for basecase/induction" lines -
    None when that step's own line never appeared at all (a crash before
    that step ran, e.g. a syntax error the DONE-line check above already
    would have caught first for a total crash, but a partial one - one step
    running, the other never started - still leaves this None)."""
    out: dict[str, str | None] = {"basecase": None, "induction": None}
    for m in TASK_STATUS_RE.finditer(output):
        status, step = m.group(1), m.group(2)
        out[step] = status
    return out


def check_cut_model(signal: str, smt_cases: dict[str, dict],
                    smt_sub: dict[str, str | None]) -> None:
    """Refuse unless the smt task ran the cut's own self-check assert
    (`<signal>` + ASYNC_CUT_CHECK_SUFFIX, see cut_async_reset_nets) and
    found no counterexample to it: a failed one means clk2fflogic models
    the flop differently from the pre-reset value the cut feeds its ARST
    cone, so every verdict in this run would rest on a wrong model."""
    case = smt_cases.get(signal + ASYNC_CUT_CHECK_SUFFIX)
    if case is None:
        raise CheckError(
            f"formal.async_reset_cuts: the cut of {signal!r} carries a "
            "self-check assert sby never reported - the cut model did not "
            "run as built" + ASYNC_CUT_FIX)
    if case["failed"] and smt_sub["basecase"] != "pass":
        raise CheckError(
            f"formal.async_reset_cuts: the cut of {signal!r} no longer "
            "matches how this yosys's clk2fflogic models the flop (its "
            "self-check assert has a counterexample) - no verdict here can "
            "be trusted until check_formal.py's cut_async_reset_nets is "
            "brought in line with it")


def classify_property(label: str, rid: str, smt_case: dict,
                      smt_sub: dict[str, str | None], pdr_status: str,
                      depth: int) -> tuple[str, dict | None]:
    """One property's verdict - "proven"/"bounded"/"failed" - and the
    violation (if any) to report it with. Pure (no I/O, no sby, no XML
    parsing of its own) so the classification RULES are testable without a
    real solver run - see this module's own header for what each rule
    means; CheckError here is the "sby genuinely never reached a verdict at
    all" case, never silently folded into any of the three outcomes."""
    # sby marks the property <failure> for an induction-step trace too
    # (trace_induct.vcd): an arbitrary, possibly unreachable start state,
    # not a counterexample. Only a basecase that did not pass is one.
    if smt_case["failed"] and smt_sub["basecase"] != "pass":
        return "failed", checklib.violation(
            "formal", "error", None, None, "property_failed", [rid],
            f"requirement {rid} (property {label}): sby found a "
            "counterexample", "sby-smtbmc")
    if pdr_status not in ("PASS", "FAIL"):
        # ERROR or UNKNOWN (a solver crash, a timeout, an unusable model)
        # used to fall through to the `basecase == "pass"` check below and
        # come back "bounded" - a passing outcome - even though the second
        # opinion gates.yaml calls for (pdr as corroboration) never actually
        # ran. Refused here, before that fallback ever sees it.
        raise CheckError(
            f"sby (pdr task) reached no verdict for property {label} "
            f"(requirement {rid}): pdr_status={pdr_status!r} is neither "
            "PASS nor FAIL - never silently folded into bounded/proven")
    if pdr_status == "FAIL":
        return "failed", checklib.violation(
            "formal", "error", None, None, "engine_disagreement", [rid],
            f"requirement {rid} (property {label}): abc pdr found a "
            "counterexample smtbmc's k-induction did not", "sby-abc-pdr")
    if smt_sub["basecase"] == "pass" and smt_sub["induction"] == "pass" \
            and pdr_status == "PASS":
        return "proven", None
    if smt_sub["basecase"] == "pass":
        return "bounded", checklib.violation(
            "formal", "info", None, None, "bounded_not_proven", [rid],
            f"requirement {rid} (property {label}): no counterexample "
            f"found to depth {depth} (spec.yaml formal.depth), but "
            "induction did not converge - "
            "recorded as bounded, never as proven", "sby-smtbmc", depth=depth)
    raise CheckError(
        f"sby (smt task) reached no verdict for property {label} "
        f"(requirement {rid}): basecase={smt_sub['basecase']!r} "
        f"induction={smt_sub['induction']!r}")


def vacuity_verdict(label: str, rid: str, verdict: str, violation,
                    cov_case: dict, cover_depth: int, cover_key: str):
    """(verdict, violation) after the cov task's cover of this assert
    (`chformal -assert2cover`). A proven or bounded assert whose cover was
    never reached is "vacuous", an error: no trace enables it, so its pass
    checked nothing. A failed one stays failed (its counterexample already
    reached it). Pure."""
    # brief: "a vacuous pass must not read as a pass" - cov_case["failed"]
    # is the assert's cover NOT reached
    if verdict == "failed" or not cov_case["failed"]:
        return verdict, violation
    return "vacuous", checklib.violation(
        "formal", "error", None, None, "vacuous_pass", [rid],
        f"requirement {rid} (property {label}): passed only vacuously - no "
        f"trace within {cover_depth} steps (spec.yaml formal.{cover_key}) "
        "ever enables this assert, so it checked nothing; an assume or the "
        "assert's own condition rules out every state it guards (with "
        "several clocks, check formal.multiclock is on). Fix the "
        "assumptions, or raise formal.cover_depth if the state is reachable "
        "only later", "sby-smtbmc", depth=cover_depth)


def wrong_kind_labels(props: dict[str, str],
                      smt_cases: dict[str, dict]) -> list[str]:
    """`property:` labels among `props` whose sby-model testcase is not an
    ASSERT (a COVER point named as if it were one) - `skipped` is NOT the
    same signal and must never be folded into this (see the comment above
    this function's one call site in run()): a `skipped` ASSERT still
    belongs to classify_property's task-level basecase/induction fallback,
    only a genuine type mismatch belongs here. Pure, like
    classify_property, so the rule is testable without a real solver run."""
    return [label for label in props
           if smt_cases[label].get("type") != "ASSERT"]


def resolve_labels(props: dict[str, str], cases: dict[str, dict]
                   ) -> tuple[dict[str, str], list[str]]:
    """({label: sby id}, [ambiguous labels]). sby's id is the property's
    hierarchical name - `LABEL` in the wrapper itself, `dut.u_chk.LABEL`
    for one a `bind` put inside the DUT, `blk.LABEL` inside a named block -
    so a label matches an exact id first, else an id whose last dotted
    component it is. Two such ids is ambiguous, never a guess. Pure."""
    ids: dict[str, str] = {}
    ambiguous = []
    for label in props:
        if label in cases:
            ids[label] = label
            continue
        hits = [pid for pid in cases if pid.rsplit(".", 1)[-1] == label]
        if len(hits) > 1:
            ambiguous.append(label)
        elif hits:
            ids[label] = hits[0]
    return ids, ambiguous


def parse_testcases(xml_path: Path) -> dict[str, dict]:
    """{id: {"type": "ASSERT"|"COVER", "failed": bool, "skipped": bool}} for
    every named property testcase sby's own JUnit XML carries - the id sby
    itself assigns from the Verilog statement label, never a text search
    over formal/*.sv (a `bind` that silently failed to attach still leaves
    formal/*.sv readable, but sby's own model then has no such id at all -
    proved empirically, docs/design.md "### M3.")."""
    if not xml_path.is_file():
        raise CheckError(f"sby produced no {xml_path.name}")
    tree = ET.parse(xml_path)
    out: dict[str, dict] = {}
    for tc in tree.iter("testcase"):
        pid = tc.attrib.get("id")
        if not pid:
            continue  # the "build execution" bookkeeping testcase, no id
        out[pid] = {
            "type": tc.attrib.get("type"),
            "failed": tc.find("failure") is not None,
            "skipped": tc.find("skipped") is not None,
        }
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = spec.get("top")
    if not isinstance(top, str) or not top.strip():
        raise CheckError("spec.yaml has no non-empty 'top' - run spec_lint "
                         "first")
    props = formal_requirements(spec)
    if not props:
        raise CheckError("no requirement has check: formal|both - nothing "
                         "for the formal gate to prove (an empty property "
                         "set is a refusal, never a pass)")

    formal_top = (spec.get("formal") or {}).get("top") or f"{top}_formal"
    missing = depth_missing_violation(spec)
    if missing is not None:
        # nothing runs on a depth nobody chose, and the gate still fails -
        # but as a finding the fix loop can route to the property-writer
        return checklib.report(
            SCRIPT, ws / "rtl", [missing], top=top, formal_top=formal_top,
            depth=None, cover_depth=None, proven=[], bounded=[], failed=[],
            vacuous=[]), args.out
    settings = formal_settings(spec)
    depth, cover_depth = settings["depth"], settings["cover_depth"]

    sv_files = collect_sources(ws, "formal", (".sv", ".v"))
    rtl_files = collect_sources(ws, "rtl")
    frontend, frontend_why, slang_so, probe_log = pick_frontend(
        sv_files, rtl_files, formal_top)
    multiclock, multiclock_why = needs_multiclock(clocking(probe_log), spec)

    sby_dir = ws / SBY_SUBDIR
    sby_dir.mkdir(parents=True, exist_ok=True)
    for stale in sby_dir.glob("*"):
        if stale.is_dir():
            import shutil
            shutil.rmtree(stale)

    cuts = async_reset_cuts(spec)
    cut_il = applied_cuts = None
    if cuts:
        cut_il, applied_cuts = build_cut_il(
            sby_dir, frontend, sv_files, rtl_files, formal_top, slang_so,
            cuts)

    tasks = {
        "smt": ("prove", "smtbmc yices"),
        "pdr": ("prove", "abc pdr"),
        "cov": ("cover", "smtbmc yices"),
    }
    outputs: dict[str, str] = {}
    testcases: dict[str, dict[str, dict]] = {}
    for name, (mode, engine) in tasks.items():
        config = sby_dir / f"{name}.sby"
        cover = mode == "cover"
        write_sby(config, sv_files, rtl_files, formal_top, mode, engine,
                  cover_depth if cover else depth, frontend, slang_so,
                  multiclock=multiclock, vacuity=cover, cut_il=cut_il)
        output, workdir = run_sby(
            sby_dir, config, name,
            settings["cover_timeout_s" if cover else "prove_timeout_s"])
        # DONE (ERROR) still matches DONE_RE - run_sby's own check only
        # catches a launcher that never reached DONE at all - but a task
        # that DID reach DONE and reached it as ERROR (a solver crash, an
        # unusable model) is refused here too, before any per-property
        # classification gets a chance to fold it into bounded/proven.
        if done_status(output) == "ERROR":
            loop = LOGIC_LOOP_RE.search(output)
            if loop is not None:
                raise CheckError(
                    f"sby ({name} task) reached DONE (ERROR): a "
                    f"combinational loop in module {loop.group(1)} "
                    "(clk2fflogic under multiclock turned a self-resetting "
                    "async loop into one) - either it is a real bug, or "
                    "declare the cut per instance in spec.yaml "
                    "formal.async_reset_cuts" + ASYNC_CUT_FIX)
            raise CheckError(
                f"sby ({name} task) reached DONE (ERROR): "
                f"{output[-2000:]}")
        outputs[name] = output
        xml_path = workdir / f"{name}.xml"
        testcases[name] = parse_testcases(xml_path)

    smt_cases, pdr_status = testcases["smt"], done_status(outputs["pdr"])
    smt_status = done_status(outputs["smt"])
    smt_sub = task_substatus(outputs["smt"])
    for cut in applied_cuts or []:
        check_cut_model(cut["signal"], smt_cases, smt_sub)

    ids, ambiguous = resolve_labels(props, smt_cases)
    if ambiguous:
        raise CheckError(
            f"spec.yaml 'property' label(s) {', '.join(sorted(ambiguous))} "
            "match more than one property in sby's own model (the same "
            "label in two scopes) - give each assert a unique label")
    missing = [label for label in props if label not in ids]
    if missing:
        raise CheckError(
            f"formal/*.sv does not define {', '.join(sorted(missing))} as "
            f"seen by sby's own model (prep -top {formal_top}) - check the "
            "assert's Verilog label matches spec.yaml's 'property' exactly, "
            f"and that it is reached ({frontend} frontend; under yosys-slang "
            "an immediate assert keeps its label only inside a named "
            "block, `always @(posedge clk) begin : blk ... end`, or as a "
            "concurrent `LABEL: assert property (...)`)")
    smt_cases = {**smt_cases, **{label: smt_cases[ids[label]]
                                 for label in props}}

    # A `property:` label spec.yaml names must be the ASSERT it claims to
    # be - a COVER testcase appears in the smt task's own model too (cover
    # points are enumerated there, just never evaluated in prove mode), so
    # `label not in smt_cases` above would not catch it: it would sail
    # through classify_property's own basecase/induction/pdr checks and
    # come back "proven" without anything ever having been proven about it.
    #
    # A `skipped` ASSERT testcase is NOT the same signal (M5, found by
    # actually running this gate on a THREE-property block - M3 only ever
    # exercised one property per run): smtbmc's k-induction reports
    # basecase/induction at the TASK level, shared across every ASSERT in
    # one sby run, not per property - when one property's induction step
    # does not converge, sby can mark OTHER, perfectly healthy properties'
    # own per-testcase XML entries `<skipped/>` too, even though the task
    # itself reached a real (if only "bounded") verdict. Treating every
    # skipped ASSERT as a label mismatch refused every property in the run
    # the moment ANY one of them needed more induction depth than it got -
    # classify_property's own basecase/induction/pdr fallback (task-level,
    # exactly what a skipped-but-still-ASSERT testcase should fall back to)
    # already handles this correctly; only a genuine kind mismatch (a COVER
    # point named as if it were an ASSERT) is refused here - see
    # wrong_kind_labels, pulled out pure (same reason classify_property is)
    # so this rule is testable without a real solver run.
    wrong_kind = wrong_kind_labels(props, smt_cases)
    if wrong_kind:
        raise CheckError(
            f"spec.yaml 'property' label(s) {', '.join(sorted(wrong_kind))} "
            "do not name an ASSERT in sby's own model (a cover point) - "
            "check: formal|both must point at an assert's own Verilog "
            "label, never a cover's")

    cov_cases = testcases["cov"]
    cover_key = ("cover_depth" if (spec.get("formal") or {}).get(
        "cover_depth") is not None else "depth")
    violations = []
    proven, bounded, failed, vacuous = [], [], [], []
    for label, rid in sorted(props.items()):
        verdict, violation = classify_property(
            label, rid, smt_cases[label], smt_sub, pdr_status, depth)
        cov_case = cov_cases.get(ids[label])
        if cov_case is None:
            raise CheckError(f"assert {ids[label]} appears in the smt model "
                             "but not as a cover in the cover task's own "
                             "model - its non-vacuity was never checked")
        verdict, violation = vacuity_verdict(
            label, rid, verdict, violation, cov_case, cover_depth, cover_key)
        {"proven": proven, "bounded": bounded, "failed": failed,
         "vacuous": vacuous}[verdict].append(rid)
        if violation is not None:
            violations.append(violation)

    covers = {pid: c for pid, c in testcases["smt"].items()
              if c["type"] == "COVER"}
    for pid in sorted(covers):
        cov_case = cov_cases.get(pid)
        if cov_case is None:
            raise CheckError(f"cover point {pid} appears in the smt model "
                             "but not in the cover task's own model")
        if cov_case["failed"]:
            violations.append(checklib.violation(
                "formal", "error", None, None, "cover_not_reached", [],
                f"cover point {pid} was not reached to depth {cover_depth} "
                f"(spec.yaml formal.{cover_key}) - if the state is "
                "reachable, raise formal.cover_depth (add it if absent) "
                "to at least the cycles its sequence needs; leave "
                "formal.depth at what the asserts need for induction - "
                "a deep prove depth makes k-induction infeasible",
                "sby-smtbmc",
                depth=cover_depth))

    async_note = (
        "smt/pdr's proven/bounded verdicts above hold for a model in which "
        "each cut flop's async clear (formal.async_reset_cuts) reads the "
        "cut flops' PRE-RESET values instead of their Q - the clear lands "
        "in the same step the flop sets, and every other reset input "
        "(rst_n) is unchanged - so they show the settled zero-delay "
        "behaviour, not the delta-cycle glitch (both flops briefly "
        "both-set before the clear catches up) or the reset pulse's "
        "width, which stay sim's job"
    ) if applied_cuts else None
    # "a declared abstraction must never read as an unqualified pass": each
    # applied cut is also an info finding, so a pass that rests on one always
    # carries it in the findings list, not only in a side field.
    for cut in applied_cuts or []:
        violations.append(checklib.violation(
            "formal", "info", None, None, "async_reset_cut_applied", [],
            f"formal model cuts the async reset of {cut['signal']} "
            f"({cut['cell_type']}): its ARST cone reads the cut flops' "
            "pre-reset values, not their Q "
            f"(spec.yaml formal.async_reset_cuts, why: {cut['why']}) - "
            "every verdict here is the settled zero-delay clear; the "
            "glitch and the reset pulse width stay with sim",
            "check_formal", signal=cut["signal"]))
    payload = checklib.report(
        SCRIPT, ws / "rtl", violations, top=top, formal_top=formal_top,
        depth=depth, cover_depth=cover_depth, frontend=frontend, frontend_why=frontend_why,
        multiclock=multiclock, multiclock_why=multiclock_why,
        proven=sorted(proven), bounded=sorted(bounded),
        failed=sorted(failed), vacuous=sorted(vacuous),
        smt_status=smt_status, pdr_status=pdr_status,
        cover_points=sorted(covers),
        async_reset_cuts=applied_cuts or [],
        async_reset_cuts_note=async_note)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
