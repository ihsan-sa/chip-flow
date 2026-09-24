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
