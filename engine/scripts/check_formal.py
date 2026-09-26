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
dotted implicit identifier, formal/*.sv holds a `bind`, or native's model
has fewer property cells than slang's. Slang needed but missing from the
toolchain, or unable to read the design, is a refusal - never a quiet fall
back to native. Under slang a property's sby id is its hierarchical name
(`dut.u_chk.LABEL`, `blk.LABEL`); a `property:` label matches an exact id,
else the one id whose last dotted component it is (two is refused as
ambiguous). The report carries `frontend` and `frontend_why`.

spec.yaml names which wrapper module to prep (`formal: {top, depth}`) and which
requirements that wrapper must prove (`check: formal|both` requirements
each carry a `property:` label matching an assert's own Verilog statement
label in formal/*.sv).

Three sby TASKS, same model, over the same depth:
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
       <skipped> there instead).

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

Refuses (CheckError, never a pass) rather than reports a finding when: no
requirement in spec.yaml has check: formal|both (an empty property set -
docs/design.md section 2's own silent-failure concern, applied here: a
formal gate with nothing to prove is not vacuously clean, it never ran);
a `property:` label spec.yaml names is not found as an ASSERT testcase in
sby's own model (the id sby echoes back, not a text search - a rename, or
under slang an immediate assert outside a named block, leaves the id
missing); a label that matches two ids; slang needed but unavailable or
unable to read the design (see Frontend above); any sby task fails to reach a DONE line at all (a crashed
launcher, a solver missing, a syntax error before the model even builds).
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
DEFAULT_DEPTH = 20
TIMEOUT_S = 180.0

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
             frontend: str = "native", slang_so: Path | None = None) -> None:
    files = "\n".join(str(f.resolve()) for f in (*sv_files, *rtl_files))
    reads = yosys_reads(frontend, sv_files, rtl_files, formal_top, slang_so)
    path.write_text(f"""\
[options]
mode {mode}
depth {depth}

[engines]
{engine}

[script]
{reads}
prep -top {formal_top}

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


def count_properties(log: str, formal_top: str) -> int | None:
    """Property cells `select -list` printed for the flattened top, or None
    when the probe never reached that command (a parse error)."""
    if PROBE_MARK not in log:
        return None
    return len(re.findall(rf"(?m)^{re.escape(formal_top)}/\S+$", log))


def probe(frontend: str, sv_files: list[Path], rtl_files: list[Path],
          formal_top: str, slang_so: Path | None) -> str:
    script = (yosys_reads(frontend, sv_files, rtl_files, formal_top, slang_so)
              .replace("\n", "; ")
              + f"; hierarchy -top {formal_top}; proc; flatten; "
              f"log {PROBE_MARK}; select -list {PROPERTY_CELLS}")
    try:
        proc = subprocess.run([str(EDA_BIN), "yosys", "-p", script],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"yosys ({frontend} probe) timed out: {exc}") from exc
    return (proc.stdout or "") + (proc.stderr or "")


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
                  formal_top: str) -> tuple[str, str | None, Path | None]:
    """(frontend, why slang was needed or None, slang .so). Native unless
    it would be unsound: a hierarchical reference it leaves undriven, a
    `bind` it drops, or fewer properties in its flattened model than
    slang's (anything else it dropped). Slang needed but missing or unable
    to read the design is a refusal - never a quiet fall back to native."""
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
    elif native_n is not None and slang_n is not None and slang_n > native_n:
        why = (f"yosys's native frontend keeps {native_n} property cell(s) "
               f"where yosys-slang keeps {slang_n}")
    if why is None:
        return "native", None, None
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
    return "slang", why, slang_so


def run_sby(sby_dir: Path, config: Path, workdir_name: str) -> tuple[str, Path]:
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
            encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"sby ({workdir_name}) timed out after "
                         f"{TIMEOUT_S:g}s: {exc}") from exc
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
            f"found to depth {depth}, but induction did not converge - "
            "recorded as bounded, never as proven", "sby-smtbmc", depth=depth)
    raise CheckError(
        f"sby (smt task) reached no verdict for property {label} "
        f"(requirement {rid}): basecase={smt_sub['basecase']!r} "
        f"induction={smt_sub['induction']!r}")


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

    formal_cfg = spec.get("formal") or {}
    formal_top = formal_cfg.get("top") or f"{top}_formal"
    depth = int(formal_cfg.get("depth", DEFAULT_DEPTH))

    sv_files = collect_sources(ws, "formal", (".sv", ".v"))
    rtl_files = collect_sources(ws, "rtl")
    frontend, frontend_why, slang_so = pick_frontend(
        sv_files, rtl_files, formal_top)

    sby_dir = ws / SBY_SUBDIR
    sby_dir.mkdir(parents=True, exist_ok=True)
    for stale in sby_dir.glob("*"):
        if stale.is_dir():
            import shutil
            shutil.rmtree(stale)

    tasks = {
        "smt": ("prove", "smtbmc yices"),
        "pdr": ("prove", "abc pdr"),
        "cov": ("cover", "smtbmc yices"),
    }
    outputs: dict[str, str] = {}
    testcases: dict[str, dict[str, dict]] = {}
    for name, (mode, engine) in tasks.items():
        config = sby_dir / f"{name}.sby"
        write_sby(config, sv_files, rtl_files, formal_top, mode, engine, depth,
                  frontend, slang_so)
        output, workdir = run_sby(sby_dir, config, name)
        # DONE (ERROR) still matches DONE_RE - run_sby's own check only
        # catches a launcher that never reached DONE at all - but a task
        # that DID reach DONE and reached it as ERROR (a solver crash, an
        # unusable model) is refused here too, before any per-property
        # classification gets a chance to fold it into bounded/proven.
        if done_status(output) == "ERROR":
            raise CheckError(
                f"sby ({name} task) reached DONE (ERROR): "
                f"{output[-2000:]}")
        outputs[name] = output
        xml_path = workdir / f"{name}.xml"
        testcases[name] = parse_testcases(xml_path)

    smt_cases, pdr_status = testcases["smt"], done_status(outputs["pdr"])
    smt_status = done_status(outputs["smt"])
    smt_sub = task_substatus(outputs["smt"])

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

    violations = []
    proven, bounded, failed = [], [], []
    for label, rid in sorted(props.items()):
        verdict, violation = classify_property(
            label, rid, smt_cases[label], smt_sub, pdr_status, depth)
        {"proven": proven, "bounded": bounded, "failed": failed}[verdict].append(rid)
        if violation is not None:
            violations.append(violation)

    cov_cases = testcases["cov"]
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
                f"cover point {pid} was not reached to depth {depth}",
                "sby-smtbmc"))

    payload = checklib.report(
        SCRIPT, ws / "rtl", violations, top=top, formal_top=formal_top,
        depth=depth, frontend=frontend, frontend_why=frontend_why,
        proven=sorted(proven), bounded=sorted(bounded),
        failed=sorted(failed), smt_status=smt_status, pdr_status=pdr_status,
        cover_points=sorted(covers))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
