"""simlib.py - ngspice bench plumbing for the analog engine (docs/design.md
1.3, 1.5, "### M8."): `.bounds.json` sidecars, `.measure` parsing, deck
templating for corners/sizing, and ngspice failure detection.

Ported from /hwde's scripts/lib/simlib.py in BEHAVIOR, not mechanism. hwde
drives ngspice IN-PROCESS through InSpice's shared-library binding
(NgSpiceShared) and never even inspects an exit code - failure there is
bounds-driven only, off measures parsed from ngspice's own stdout and a
`.measure ... failed!` line parsed from stderr (load_bounds/compare_bounds/
parse_measures/parse_failed_measures below are that logic, carried over
near verbatim - the sidecar shape and the two regexes are unchanged).
chip-flow shells out through `eda ngspice -b <deck>` instead (design.md
1.2's launcher; 1.3: "now through `eda ngspice -b`"), which drops the whole
InSpice/DLL layer but ALSO drops the one thing an in-process binding gave
hwde for free: a Python exception on a bad run. CLI batch ngspice hands
back nothing but text and an exit code - and the exit code lies.

Confirmed empirically against this box's real gf180mcuD models, not a
guess (see docs/spikes/dcosim.md for the earlier, related finding under a
different bridge): a MOSFET gate left floating (no DC path to any supply)
drives ngspice through "singular matrix" -> "Dynamic gmin stepping
failed" -> "True gmin stepping failed" -> "source stepping failed" - every
one of those a WARNING on stderr, never an Error - and ngspice still
finishes the requested `.op`, prints v(d) and v(floatgate) as if nothing
happened, and exits 0. A truly unresolvable model name (a real parse-time
error) DOES exit nonzero with an "Error:" line on stderr - but nothing
about the exit code alone lets a caller tell a clean run, a recovered-but-
meaningless run, and a real parse failure apart from each other without
reading the text. ENGINE_ERROR_PATTERNS below is the new safety net this
gate needs that hwde's PCB-level (op-amp/divider/zener) benches never had
to build: gate.py's own contract ("a gate whose tool could not run is exit
2, never a pass") and this milestone's specific brief both require that a
run which errored, failed to converge, or produced a measure it never
printed must fail the sim gate - not merely look clean because ngspice
said so.

Deck templating (materialize, new at M8): a bench/netlist file is plain
SPICE with `{{TOKEN}}` placeholders - `{{PDK}}` (the PDK root, e.g. for a
`.lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' {{CORNER}}` line),
`{{CORNER}}` (an ngspice `.lib` section name from corners.yaml: typical,
ss, ff, sf, fs), `{{TEMP_C}}`, `{{VDD}}`, and `{{SIZING}}` (a generated
`.param name=value ...` line built from a block's optional sizing/
sizing.yaml, section 4's optimise target - empty when the block has none).
Double braces are deliberate: ngspice's OWN expression syntax is a single
`{expr}` (e.g. `w={wn}`), so `{{...}}` cannot collide with real SPICE.
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

from checklib import CheckError, load_json, violation

# ---------------------------------------------------------------- measures

# Ported verbatim from hwde's simlib.py (module docstring above):
#   "  Measurements for Transient Analysis" opens a results block; the next
#   non-matching, non-blank line closes it. Confirmed against this box's own
#   ngspice: a real run prints exactly "  Measurements for Transient
#   Analysis" (also "... for DC Analysis", "... for AC Analysis").
_MEASURE_HEADER = re.compile(r"^\s*Measurements for\b", re.IGNORECASE)
_MEASURE_LINE = re.compile(
    r"^\s*([A-Za-z_][\w.]*)\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
# ".measure tran never_happens find v(d) when v(d)=99 failed!" (stderr) -
# confirmed verbatim against this box's real ngspice output.
_FAILED_LINE = re.compile(
    r"^\s*\.meas(?:ure)?\s+\S+\s+(\S+)\s.*failed!\s*$", re.IGNORECASE)


def parse_measures(stdout_text: str) -> dict[str, float]:
    """{measure_name.lower(): value} from ngspice's own `.measure` results
    block(s) in stdout. Ported from hwde's simlib.parse_measures."""
    measures: dict[str, float] = {}
    in_block = False
    for line in stdout_text.splitlines():
        if _MEASURE_HEADER.match(line):
            in_block = True
            continue
        if not in_block:
            continue
        m = _MEASURE_LINE.match(line)
        if m:
            measures[m.group(1).lower()] = float(m.group(2))
            continue
        if line.strip():
            in_block = False
    return measures


def parse_failed_measures(stderr_text: str) -> set[str]:
    """{measure_name.lower(), ...} whose FIND/WHEN trigger was never met -
    ngspice reports this on STDERR, not stdout (confirmed empirically).
    Ported from hwde's simlib.parse_failed_measures."""
    return {m.group(1).lower() for line in stderr_text.splitlines()
           for m in [_FAILED_LINE.match(line)] if m}


# ------------------------------------------------------------ engine errors

# New at M8 (no /hwde precedent - see module docstring). Every pattern here
# was either produced by a real `eda ngspice -b` run against this box's own
# gf180mcuD models (singular_matrix, gmin/source stepping, unknown_subckt,
# empty_netlist - all confirmed live) or is documented, well-known ngspice
# failure text this box has not needed to reproduce locally yet
# (timestep_too_small, trouble_with_node - both named in docs/spikes/
# dcosim.md's own real gf180 convergence failure under a different bridge;
# too_many_iterations, no_such_device, no_such_node). Matched case-
# insensitively against stdout+stderr combined - ngspice is not consistent
# about which stream a given message lands on (unknown-model errors and the
# floating-node warnings above both went to stderr in practice, but nothing
# guarantees every build/version agrees).
ENGINE_ERROR_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"singular matrix", re.I), "singular_matrix"),
    (re.compile(r"gmin stepping failed", re.I), "gmin_stepping_failed"),
    (re.compile(r"source stepping failed", re.I), "source_stepping_failed"),
    (re.compile(r"timestep too small", re.I), "timestep_too_small"),
    (re.compile(r"trouble with node", re.I), "convergence_trouble"),
    (re.compile(r"too many iterations", re.I), "too_many_iterations"),
    (re.compile(r"no such device", re.I), "no_such_device"),
    (re.compile(r"no such node", re.I), "no_such_node"),
    (re.compile(r"unknown subckt", re.I), "unknown_subckt"),
    (re.compile(r"unknown model", re.I), "unknown_model"),
    (re.compile(r"incomplete or empty netlist", re.I), "empty_netlist"),
    (re.compile(r"simulation interrupted", re.I), "simulation_interrupted"),
    (re.compile(r"^\s*error[: ]", re.I | re.M), "ngspice_error"),
]


def detect_engine_errors(text: str) -> list[str]:
    """Sorted, deduplicated list of ENGINE_ERROR_PATTERNS kinds found in
    `text` (stdout+stderr combined). Empty means no known failure text was
    seen - NOT proof the run is trustworthy on its own; callers still run
    compare_bounds() over whatever measures did come back."""
    return sorted({kind for pat, kind in ENGINE_ERROR_PATTERNS
                  if pat.search(text)})


def engine_error_violations(check: str, testbench: str, corner: str,
                            kinds: list[str], detail: str) -> list[dict]:
    return [violation(check, "error", testbench, None,
                      f"sim_engine_error_{k}", [corner],
                      f"ngspice reported {k.replace('_', ' ')} at corner "
                      f"{corner!r}: {detail}", "ngspice")
           for k in kinds]


# -------------------------------------------------------------------- bounds

def load_bounds(path: Path) -> list[dict]:
    """Load+validate a `.bounds.json` sidecar. Ported from hwde's
    simlib.load_bounds: a non-empty JSON list of {measure, min?, max?,
    severity?, msg?} - at least one of min/max required, each a finite
    non-bool number when present, severity in {error, warning} (default
    error)."""
    data = load_json(path, "bounds sidecar")
    if not isinstance(data, list) or not data:
        raise CheckError(f"{path}: bounds sidecar must be a non-empty JSON "
                         "list")
    out = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict) or not entry.get("measure"):
            raise CheckError(f"{path}[{i}]: needs a non-empty 'measure'")
        name = entry["measure"]
        has_min, has_max = "min" in entry, "max" in entry
        if not has_min and not has_max:
            raise CheckError(f"{path}[{i}] ({name}): needs 'min' and/or 'max'")
        for key in ("min", "max"):
            if key not in entry:
                continue
            v = entry[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(v):
                raise CheckError(f"{path}[{i}] ({name}): {key!r} must be a "
                                 "finite number")
        sev = entry.get("severity", "error")
        if sev not in ("error", "warning"):
            raise CheckError(f"{path}[{i}] ({name}): severity must be "
                             "'error' or 'warning'")
        out.append({**entry, "severity": sev})
    return out


def compare_bounds(bounds: list[dict], measures: dict[str, float],
                   testbench: str, corner: str = "tt",
                   failed_measures: set[str] | None = None,
                   check: str = "sim") -> list[dict]:
    """Ported from hwde's simlib.compare_bounds, `corner` added (every
    finding names which corner it failed at - sim_pvt runs several).

    A bound whose measure name is not in `measures` is `sim_measure_missing`
    at that bound's own severity - this is what makes "a measure it never
    printed" a failure rather than a silent pass, whether ngspice never ran
    the `.measure` at all or explicitly reported it FAILED (failed_measures,
    from parse_failed_measures - distinguished only in the message text,
    the fault either way)."""
    failed_measures = failed_measures or set()
    out: list[dict] = []
    for b in bounds:
        name = b["measure"]
        key = name.lower()
        if key in failed_measures:
            out.append(violation(
                check, b["severity"], testbench, None, "sim_measure_missing",
                [name, corner],
                f"{name} @ {corner}: ngspice reported the .measure trigger/"
                "target condition was never met ('failed!') - never "
                "printed a value", "ngspice"))
            continue
        if key not in measures:
            out.append(violation(
                check, b["severity"], testbench, None, "sim_measure_missing",
                [name, corner],
                f"{name} @ {corner}: not present in ngspice's output - "
                "never printed", "ngspice"))
            continue
        val = measures[key]
        lo, hi = b.get("min"), b.get("max")
        out_of_range = (not math.isfinite(val)
                       or (lo is not None and val < lo)
                       or (hi is not None and val > hi))
        if out_of_range:
            out.append(violation(
                check, b["severity"], testbench, None, "sim_bound_fail",
                [name, corner],
                f"{name} @ {corner} = {val:.6g} outside bound "
                f"[{lo if lo is not None else '-inf'}, "
                f"{hi if hi is not None else '+inf'}]"
                + (f" ({b['msg']})" if b.get("msg") else ""), "ngspice"))
    return out


# ------------------------------------------------------------- deck templates

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def materialize(template_text: str, subs: dict[str, str]) -> str:
    """Replace every `{{TOKEN}}` with subs[TOKEN]; a token the caller did
    not supply raises CheckError (a bench referencing a placeholder
    sim_run.py never filled in is a broken bench, not a silently-blank
    one)."""
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in subs:
            raise CheckError(
                f"deck template references {{{{{key}}}}} with no "
                "substitution provided")
        return str(subs[key])
    return _PLACEHOLDER_RE.sub(repl, template_text)


def sizing_param_line(sizing: dict) -> str:
    """A `.param name=value ...` line from a sizing/sizing.yaml mapping
    ({name: {value, min?, max?}} - engine/scripts/optimise.py's own shape).
    Empty sizing -> a harmless comment, so `{{SIZING}}` always resolves to
    valid SPICE."""
    if not sizing:
        return "* no sizing overrides"
    parts = []
    for name, spec in sizing.items():
        val = spec["value"] if isinstance(spec, dict) else spec
        parts.append(f"{name}={val!r}" if isinstance(val, str)
                     else f"{name}={val:.6g}")
    return ".param " + " ".join(parts)


# ------------------------------------------------------------------- running

def run_ngspice(eda_bin: Path, deck_path: Path, cwd: Path,
                timeout: float) -> tuple[str, str, int]:
    """Run `eda ngspice -b <deck>`, returning (stdout, stderr, returncode).
    NEVER interpreted here - every caller must run detect_engine_errors()
    and compare_bounds() over the text regardless of returncode (module
    docstring: the exit code lies)."""
    try:
        proc = subprocess.run(
            [str(eda_bin), "ngspice", "-b", str(deck_path)], cwd=str(cwd),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
        return proc.stdout or "", proc.stderr or "", proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else (
            exc.stdout.decode("utf-8", "replace") if exc.stdout else "")
        err = exc.stderr if isinstance(exc.stderr, str) else (
            exc.stderr.decode("utf-8", "replace") if exc.stderr else "")
        return out, err + f"\n[sim_run] timed out after {timeout:g}s", -1
