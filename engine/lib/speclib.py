"""speclib.py - spec.yaml parsing and lint rules (docs/design.md 1.4, 1.5).

New at M2 (docs/design.md 1.3: "New: ... speclib ..."): nothing to port, this
is chip-flow's own shape, not /hwde's. spec.yaml is a block's machine-readable
half of its spec, and `spec_lint` (engine/scripts/check_spec_lint.py, gates.yaml
row of the same name) refuses a block without one.

Schema this module reads (docs/design.md 1.4 plus one field the design leaves
implicit: every other gate that names a top module - lint, sim, mutate,
synth, harden - needs one *somewhere*, and spec.yaml, read before any RTL
exists, is the only artifact early enough):

    top: str                    the top module name (new here; 1.4 does not
                                 spell this out, but every later gate needs it
                                 from somewhere, and this is the earliest
                                 artifact that exists)
    requirements:
      - id: str                 unique, non-empty
        text: str                non-empty prose
        check: sim|formal|both|measure
        bounds: {...}            required when check == measure
    ports: {name: {dir, width}} optional, loose
    tt_pins: {name: pin}         optional (M4+); a non-empty mapping when present
    clock: {period_ns, domains}  optional, loose
    must_keep: [str]             optional

Pass criteria (gates.yaml `spec_lint` row): "Every requirement has an id, a
check kind and, for `measure`, bounds; tt_pins fit the tile." The tile-fit
half needs the TT template (M4, engine/reference/tt/) and is out of scope
until then - lint_spec here validates everything that does not need it, and
raises nothing tile-shaped; a tt_pins mapping is required to be a real,
non-empty mapping when present, but never checked against a tile's pin count.

Every violation is checklib.violation()-shaped so check_spec_lint.py can
hand the list straight to checklib.report().
"""
from __future__ import annotations

from pathlib import Path

import yaml

CHECK_KINDS = {"sim", "formal", "both", "measure"}

# ---------------------------------------------------------------- /ade (M8)
# New at M8 (docs/design.md "### M8."): ade's own spec.yaml shape, on top of
# the fields lint_spec above already validates generically (top, clock,
# must_keep). gates.yaml's ade `spec_lint` row: "Every measure has bounds
# and a corner set; supply and devices declared." - the fault it must catch:
# "a measure without bounds".
#
#     supply: {vdd: float}            required; the nominal rail sim_run.py
#                                      resolves every corner's VDD from
#     devices: [str, ...]             required, non-empty; refdes this
#                                      block's netlist/*.cir must instantiate
#                                      (check_netlist_lint.py cross-checks)
#                                      and bench_strength mutates
#     corners: "default" | [str,...]  optional (default: corners.py's own
#                                      default_corners())
#     measures:
#       - name: str                   required, unique
#         bounds: {min?, max?}        required, at least one of min/max
#         corners: "default"|[str,...]|"all"   optional (default: "default")
#         severity: error|warning     optional (default: error)
#     mc: {enabled: bool, runs?: int, yield_min?: float, global?: bool,
#          seed?: int}   optional - runs?/seed? are check_mc.py's own
#                        per-run count and seed base (`.option seed=<seed
#                        base + i>` per run, `runs: 0` refused outright);
#                        global? asks check_mc.py to also force
#                        `sw_stat_global=1` (process-level MC), not just
#                        `sw_stat_mismatch=1` (device-level, the default)

ADE_MEASURE_CORNER_KINDS = {"default", "all"}


def lint_spec_ade(spec: dict, rel_path: str = "spec/spec.yaml") -> list[dict]:
    """Return the list of ade spec_lint violations - [] means a clean spec.
    Deliberately independent of lint_spec() above (vde's own): that
    function requires a non-empty `requirements` list and validates
    `tt_pins`, neither of which gates.yaml's ade spec_lint row asks for
    ("Every measure has bounds and a corner set; supply and devices
    declared.") - reusing it wholesale would force every analog block to
    also carry digital-shaped requirements it has no use for. `top` is
    checked here too (every gate downstream needs it, same as vde's)."""
    from checklib import violation

    out: list[dict] = []

    def bad(kind: str, msg: str, refs=None, severity="error"):
        out.append(violation("spec_lint", severity, rel_path, None, kind,
                             refs or [], msg, "speclib"))

    top = spec.get("top")
    if not isinstance(top, str) or not top.strip():
        bad("no_top", "spec.yaml has no non-empty 'top' block name")

    supply = spec.get("supply")
    if not isinstance(supply, dict) or not isinstance(supply.get("vdd"), (int, float)) \
            or isinstance(supply.get("vdd"), bool) or supply.get("vdd", 0) <= 0:
        bad("no_supply", "spec.yaml has no 'supply.vdd' (a positive number)")

    devices = spec.get("devices")
    if not isinstance(devices, list) or not devices \
            or not all(isinstance(d, str) and d.strip() for d in devices):
        bad("no_devices", "spec.yaml has no non-empty 'devices' list of "
                          "refdes strings")

    corners_field = spec.get("corners", "default")
    if not ((isinstance(corners_field, str) and corners_field in ADE_MEASURE_CORNER_KINDS)
           or (isinstance(corners_field, list) and corners_field
               and all(isinstance(c, str) for c in corners_field))):
        bad("bad_corners", "spec.yaml 'corners' must be 'default', 'all', "
                           "or a non-empty list of corner names")

    measures = spec.get("measures")
    if not isinstance(measures, list) or not measures:
        # this is the fault gates.yaml names for ade's spec_lint: "a
        # measure without bounds" widened one notch - no measures at all is
        # the same failure at its most extreme.
        bad("no_measures", "spec.yaml has no non-empty 'measures' list")
        measures = []

    seen: set[str] = set()
    for i, m in enumerate(measures):
        where = f"measures[{i}]"
        if not isinstance(m, dict):
            bad("measure_not_a_mapping", f"{where} is not a mapping")
            continue
        name = m.get("name")
        if not isinstance(name, str) or not name.strip():
            bad("measure_no_name", f"{where} has no non-empty 'name'")
            name = None
        elif name in seen:
            bad("measure_dup_name", f"measure name {name!r} is not unique",
               refs=[name])
        else:
            seen.add(name)

        bounds = m.get("bounds")
        if not isinstance(bounds, dict) or not ("min" in bounds or "max" in bounds):
            bad("measure_no_bounds",
               f"{where} (name {name!r}) has no 'bounds' with 'min' and/or "
               "'max'", refs=[name] if name else [])

        mc = m.get("corners", "default")
        if not ((isinstance(mc, str) and mc in ADE_MEASURE_CORNER_KINDS)
               or (isinstance(mc, list) and mc
                   and all(isinstance(c, str) for c in mc))):
            bad("measure_bad_corners",
               f"{where} (name {name!r}) 'corners' must be 'default', "
               "'all', or a non-empty list of corner names",
               refs=[name] if name else [])

        sev = m.get("severity", "error")
        if sev not in ("error", "warning"):
            bad("measure_bad_severity",
               f"{where} (name {name!r}) 'severity' must be 'error' or "
               "'warning'", refs=[name] if name else [])

    mc_cfg = spec.get("mc")
    if mc_cfg is not None and not isinstance(mc_cfg, dict):
        bad("mc_not_a_mapping", "'mc' is present but not a mapping")

    return out


def lint_measures_vs_bench_bounds(spec: dict, bench_bounds: dict[str, list[dict]],
                                  rel_path: str = "spec/spec.yaml") -> list[dict]:
    """Cross-check spec.yaml's own `measures:` list against every tb/*.bounds.json
    sidecar's own measure names. design.md 1.3: "The bench-writer, in fresh
    context, writes tb/*.cir ... and a .bounds.json sidecar per bench" -
    independently of spec.yaml, so the two declarations of "what gets
    measured, with what bound" can silently drift apart with nothing
    catching it: a spec measure no bench ever scores (a requirement sim_pvt
    never actually checks, at any corner, yet the spec claims it is
    covered) or a bench bound the spec never named (scored and enforced
    every run, but not a declared requirement at all - undeclared scope no
    reviewer reading spec.yaml alone would ever see). gates.yaml's ade
    spec_lint row fault ("a measure without bounds") widened one notch:
    a measure without a BENCH bound is the same failure shape, and so is
    its mirror.

    `bench_bounds` is {bench filename: [bounds sidecar entries, already
    simlib.load_bounds()-validated]} - built by the caller (this module
    never touches the filesystem or imports sim_run/simlib, kept a plain
    lib/scripts boundary same as the rest of this file); an empty dict
    (no benches written yet - spec_lint runs right after spec-writer,
    before any bench-writer step in skills/ade/reference/tasks.yaml's own
    `spec` verb) means nothing to reconcile against yet, not a violation."""
    from checklib import violation

    out: list[dict] = []
    if not bench_bounds:
        return out

    measures = spec.get("measures")
    if not isinstance(measures, list):
        return out  # lint_spec_ade's own no_measures violation covers this

    def bad(kind: str, msg: str, refs=None, severity="error"):
        out.append(violation("spec_lint", severity, rel_path, None, kind,
                             refs or [], msg, "speclib"))

    spec_names = {m["name"] for m in measures
                 if isinstance(m, dict) and isinstance(m.get("name"), str)
                 and m["name"].strip()}
    bench_names: dict[str, list[str]] = {}
    for bench_name, bounds in bench_bounds.items():
        for b in bounds:
            name = b.get("measure")
            if isinstance(name, str) and name.strip():
                bench_names.setdefault(name, []).append(bench_name)

    for name in sorted(spec_names - bench_names.keys()):
        bad("measure_no_bench_bound",
           f"measure {name!r} is declared in spec.yaml's 'measures' but no "
           "tb/*.bounds.json sidecar scores it - a requirement with "
           "nothing actually checking it", refs=[name])

    for name in sorted(bench_names.keys() - spec_names):
        benches = ", ".join(sorted(set(bench_names[name])))
        bad("bench_bound_no_spec_measure",
           f"measure {name!r} has a tb/*.bounds.json bound ({benches}) but "
           "spec.yaml's own 'measures' list never declares it - an "
           "undeclared requirement being silently enforced every run",
           refs=[name])

    return out


def load_spec(path: Path) -> dict:
    """Parse spec.yaml. Anything that is not a YAML mapping raises - a lint
    violation needs a dict to report fields against, so a malformed file is
    an operational error (exit 2 via CheckError upstream), not a finding."""
    from checklib import CheckError

    p = Path(path)
    if not p.is_file():
        raise CheckError(f"no spec.yaml at {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CheckError(f"{p} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise CheckError(f"{p} must be a YAML mapping, got {type(data).__name__}")
    return data


def lint_spec(spec: dict, rel_path: str = "spec/spec.yaml") -> list[dict]:
    """Return the list of spec_lint violations - [] means a clean spec."""
    from checklib import violation

    out: list[dict] = []

    def bad(kind: str, msg: str, refs=None, severity="error"):
        out.append(violation("spec_lint", severity, rel_path, None, kind,
                             refs or [], msg, "speclib"))

    top = spec.get("top")
    if not isinstance(top, str) or not top.strip():
        bad("no_top", "spec.yaml has no non-empty 'top' module name")

    reqs = spec.get("requirements")
    if not isinstance(reqs, list) or not reqs:
        bad("no_requirements", "spec.yaml has no non-empty 'requirements' list")
        reqs = []

    seen_ids: set[str] = set()
    for i, req in enumerate(reqs):
        where = f"requirements[{i}]"
        if not isinstance(req, dict):
            bad("requirement_not_a_mapping", f"{where} is not a mapping")
            continue
        rid = req.get("id")
        if not isinstance(rid, str) or not rid.strip():
            bad("requirement_no_id", f"{where} has no non-empty 'id'")
            rid = None
        elif rid in seen_ids:
            bad("requirement_dup_id", f"requirement id {rid!r} is not unique",
               refs=[rid])
        else:
            seen_ids.add(rid)

        text = req.get("text")
        if not isinstance(text, str) or not text.strip():
            bad("requirement_no_text", f"{where} (id {rid!r}) has no "
                                       "non-empty 'text'", refs=[rid] if rid else [])

        check = req.get("check")
        if check not in CHECK_KINDS:
            # this is the fault gates.yaml names for spec_lint: "a
            # requirement with no way to check it".
            bad("requirement_no_check",
               f"{where} (id {rid!r}) has no valid 'check' kind (must be "
               f"one of {sorted(CHECK_KINDS)}, got {check!r})",
               refs=[rid] if rid else [])
        elif check == "measure" and not req.get("bounds"):
            bad("requirement_measure_no_bounds",
               f"{where} (id {rid!r}) has check: measure but no 'bounds'",
               refs=[rid] if rid else [])
        elif check in ("formal", "both"):
            # M3 (docs/design.md "### M3."): check_formal.py joins a
            # requirement to its assert/cover in formal/*.sv by this label,
            # never by a text search - a requirement with no 'property'
            # would otherwise have to be searched for by name, and that
            # falls apart the moment the property's own name and the
            # requirement's id can't agree (dashes are illegal in a Verilog
            # statement label, so they never can).
            prop = req.get("property")
            if not isinstance(prop, str) or not prop.strip():
                bad("requirement_formal_no_property",
                   f"{where} (id {rid!r}) has check: {check} but no "
                   "'property' label naming its assert/cover in formal/*.sv",
                   refs=[rid] if rid else [])

    tt_pins = spec.get("tt_pins")
    if tt_pins is not None and (not isinstance(tt_pins, dict) or not tt_pins):
        bad("tt_pins_empty", "'tt_pins' is present but not a non-empty mapping")

    clock = spec.get("clock")
    if clock is not None:
        if not isinstance(clock, dict):
            bad("clock_not_a_mapping", "'clock' is present but not a mapping")
        else:
            period = clock.get("period_ns")
            if not isinstance(period, (int, float)) or isinstance(period, bool) \
                    or period <= 0:
                bad("clock_bad_period",
                   "'clock.period_ns' must be a positive number")
            domains = clock.get("domains")
            if not isinstance(domains, list) or not domains:
                bad("clock_no_domains",
                   "'clock.domains' must be a non-empty list")

    return out
