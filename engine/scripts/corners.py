#!/usr/bin/env python
"""corners.py - PVT corner expansion for /ade (docs/design.md 1.4, 5,
"### M8."). New at M8: no /hwde precedent (a PCB flow has no process
corner) - reads engine/reference/corners.yaml, the raw axes and the default
sweep (that file's own header explains the pairing choice).

    corners.py [--corners-yaml PATH] [--names N1 N2 ...] [--out FILE]

With no --names, prints the default corner set (`default_corners`). With
--names, prints exactly those corners by name, in the order given - a
block's spec.yaml `corners:` list (design.md 1.4) names a subset (or
superset, via add-corner) of this file's default_corners this way, and may
name a passive_corners or passive_skew_corners entry too.

Library API (imported by sim_run.py / check_sim_pvt.py / check_bench_strength.py,
never re-implemented there):
    load(path) -> dict                       parsed + validated corners.yaml
    default_corners(data) -> list[dict]       the default sweep, validated
    corners_by_name(data, names) -> list[dict]
    grid_corners(data, grid) -> list[dict]    a spec-declared PVT grid
    spec_corners(data, field, passives=()) -> list[dict]
                                             spec.yaml `corners` -> the sweep
    passive_devices(netlist_text) -> list[str] which spread-prone passives
    passive_of(corner, device=None) -> str     the corner's passive section
    scope_out_problem(h1, dimension, quote)    why a scope-out is refused
    recorded_scope_outs(state_data) -> list    the verified H1 scope-outs
    resolve_vdd(corner, nominal_vdd) -> float  nominal * (1 + supply_pct/100)

A spec's `corners` field is one of: "default" (the five above), "all" (the
full process x temperature x supply cross product), a list of corner names
(UNIONED with the default five, never a replacement - design.md 5's "never
fewer"), or `{grid: {process: [...], temp_c: [...], supply_pct: [...]}}`, a
cross product the spec declares itself, e.g. tt/ff/ss x -40/25/125 C at a
fixed VDD. A grid REPLACES the default five, so it must still span them:
its process list holds typical, ss and ff, and its temp_c list holds the
axis' coldest and hottest points. Temperatures and supplies may be any value
inside the axis range (25 C is not an axis point, but lies inside it);
supply_pct defaults to [0], the spec's own nominal VDD. Grid corners are
named `<process>_<temp>c[_v<supply>]` (typical -> tt, a minus sign -> m):
`ss_m40c`, `tt_25c`, `ff_125c_vp10`.

Passive spread. The PDK keeps poly/diffusion resistor sheet resistance
(`.lib res_<p>`, ppolyf_u_1k +/-20%) and MIM capacitance (`.lib
mimcap_<p>`, +/-10-15%) in sections of their own, p in corners.yaml's
`passive` axis (typical, ss = more R and C, ff = less). A corner's passive
section is its `passive` key, else its own process where the PDK has one
(ss -> ss, ff -> ff) and typical otherwise (passive_of) - so the default
five only ever move R and C together with the transistors. A design whose
netlist uses a poly/diffusion resistor or a MIM cap (passive_devices) gets
corners.yaml's `passive_corners` appended to whatever its spec sweeps
(spec_corners' `passives`): typical transistors at each RC extreme.
`passive_skew_corners` (each transistor extreme at the opposite RC
extreme, the charge-pump against loop-filter corners) are swept only when
a spec's `corners` list names them - corners.yaml says why.

Scoped-out dimensions. A person may rule one passive corner dimension out
of scope at H1 (say "MIM capacitor spread is out of scope for this rung").
`state.py scope-out --dimension mim_cap --quote '<their words>'` records it
on the approved H1 record as `human.H1.scope_out`, and only when the quote
is verbatim in that record's answer or note and one clause of it
(SCOPE_CLAUSE_RE) names that dimension and no other (SCOPE_DIMENSIONS)
and says it is out of scope (SCOPE_OUT_RE) without a negation before it
(SCOPE_NEGATED_RE): the person's text is the source, never the session's. spec_corners(pinned=)
then holds that one device at typical at every corner (a corner's
`pinned` key, which passive_of(corner, device) honours) and stops adding
passive_corners on its account; every other axis, the other passive
included, is swept as before. Every reader re-verifies the record
(recorded_scope_outs), so a hand-edited state.json is a refusal.

Not a gate (no workspace, no violations) - a plain reference-data reader,
so its CLI contract is checklib's minus the pass/violations status: exit 0
on success, 2 on a bad corners.yaml or an unknown --names entry.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
from checklib import CheckError  # noqa: E402

import yaml  # noqa: E402

SCRIPT = "corners"
DEFAULT_YAML = ENGINE / "reference" / "corners.yaml"
REQUIRED_AXES = ("process", "temperature_c", "supply_pct")
# the PDK's passive sections exist for these process names only
PDK_PASSIVE = ("typical", "ss", "ff")
RESISTOR_RE = re.compile(r"^(?:[np](?:plus|polyf)_[us](?:_\w+)?|nwell)$", re.I)
MIM_RE = re.compile(r"^cap_mim_\w+$", re.I)


def load(path: Path | str = DEFAULT_YAML) -> dict:
    p = Path(path)
    if not p.is_file():
        raise CheckError(f"no corners.yaml at {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CheckError(f"{p} must be a YAML mapping")
    for axis in REQUIRED_AXES:
        vals = data.get(axis)
        if not isinstance(vals, list) or not vals:
            raise CheckError(f"{p}: '{axis}' must be a non-empty list")
    defaults = data.get("default_corners")
    if not isinstance(defaults, list) or not defaults:
        raise CheckError(f"{p}: 'default_corners' must be a non-empty list")
    passive = data.setdefault("passive", list(PDK_PASSIVE))
    if (not isinstance(passive, list) or not passive
            or not set(passive) <= set(PDK_PASSIVE)):
        raise CheckError(f"{p}: 'passive' must be a non-empty list drawn "
                         f"from the PDK's passive sections {list(PDK_PASSIVE)}")
    for key in ("passive_corners", "passive_skew_corners"):
        if not isinstance(data.setdefault(key, []), list):
            raise CheckError(f"{p}: '{key}' must be a list")
    axes = {"process": set(data["process"]),
            "temp_c": set(data["temperature_c"]),
            "supply_pct": set(data["supply_pct"]), "passive": set(passive)}
    seen_names: set[str] = set()
    for key, corners in (("default_corners", defaults),
                         ("passive_corners", data["passive_corners"]),
                         ("passive_skew_corners",
                          data["passive_skew_corners"])):
        for i, c in enumerate(corners):
            where = f"{p}: {key}[{i}]"
            if not isinstance(c, dict):
                raise CheckError(f"{where} is not a mapping")
            needed = ("name", "process", "temp_c", "supply_pct") + (
                ("passive",) if key.startswith("passive") else ())
            for k in needed:
                if k not in c:
                    raise CheckError(f"{where} missing {k!r}")
            if c["name"] in seen_names:
                raise CheckError(f"{p}: duplicate corner name {c['name']!r}")
            seen_names.add(c["name"])
            for k, allowed in axes.items():
                if k in c and c[k] not in allowed:
                    raise CheckError(f"{where} {k} {c[k]!r} not in "
                                     f"{sorted(allowed, key=str)}")
    return data


def passive_of(corner: dict, device: str | None = None) -> str:
    """The PDK passive section suffix (res_<it>, mimcap_<it>) `corner`
    simulates at: for a `device` ("resistor"/"mim_cap") the corner pins
    (a scoped-out dimension), the pinned section; else its own `passive`,
    else its process where the PDK has a passive section of that name,
    else typical (sf/fs have none)."""
    pinned = corner.get("pinned") or {}
    if device and device in pinned:
        return pinned[device]
    if corner.get("passive"):
        return corner["passive"]
    return corner["process"] if corner["process"] in PDK_PASSIVE else "typical"


def passive_devices(netlist_text: str) -> list[str]:
    """Which spread-prone passives a SPICE netlist instantiates: "resistor"
    (a poly/diffusion/well resistor subckt - ppolyf_u_1k, npolyf_s, nwell,
    ...), "mim_cap" (cap_mim_*). A subckt instance's model is its last
    token that is not a `key=value` parameter. Metal resistors are not
    counted. Pure."""
    found = set()
    joined = re.sub(r"\n[ \t]*\+", " ", netlist_text)  # continuation lines
    for line in joined.splitlines():
        tokens = line.split()
        if not tokens or tokens[0][0] not in "xX":
            continue
        names = [t for t in tokens[1:] if "=" not in t]
        model = names[-1] if names else ""
        if RESISTOR_RE.match(model):
            found.add("resistor")
        elif MIM_RE.match(model):
            found.add("mim_cap")
    return sorted(found)


def default_corners(data: dict) -> list[dict]:
    return [dict(c) for c in data["default_corners"]]


def corners_by_name(data: dict, names: list[str]) -> list[dict]:
    by_name = {c["name"]: c for c in (*data["default_corners"],
                                      *data.get("passive_corners", []),
                                      *data.get("passive_skew_corners", []))}
    out = []
    for n in names:
        if n not in by_name:
            raise CheckError(f"unknown corner {n!r}; known: "
                             f"{sorted(by_name)}")
        out.append(dict(by_name[n]))
    return out


GRID_MUST_SPAN_PROCESS = ("typical", "ss", "ff")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _tag(v) -> str:
    text = f"{v:g}"
    return ("m" + text[1:]) if text.startswith("-") else text


def grid_corners(data: dict, grid) -> list[dict]:
    if not isinstance(grid, dict):
        raise CheckError("corners.grid must be a mapping of process/temp_c/"
                         "supply_pct lists")
    unknown = sorted(set(grid) - {"process", "temp_c", "supply_pct"})
    if unknown:
        raise CheckError(f"corners.grid has unknown key(s) {unknown}; "
                         "allowed: process, temp_c, supply_pct")
    procs = grid.get("process")
    temps = grid.get("temp_c")
    supplies = grid.get("supply_pct", [0])
    for key, vals in (("process", procs), ("temp_c", temps),
                      ("supply_pct", supplies)):
        if not isinstance(vals, list) or not vals or len(set(map(str, vals))) != len(vals):
            raise CheckError(f"corners.grid.{key} must be a non-empty list "
                             "without repeats")
    bad_p = [p for p in procs if p not in data["process"]]
    if bad_p:
        raise CheckError(f"corners.grid.process {bad_p} not in "
                         f"{data['process']}")
    missing = [p for p in GRID_MUST_SPAN_PROCESS if p not in procs]
    if missing:
        raise CheckError(f"corners.grid.process lacks {missing}: a grid "
                         "replaces the default five corners, so it must "
                         f"still span {list(GRID_MUST_SPAN_PROCESS)}")
    for key, vals, axis in (("temp_c", temps, "temperature_c"),
                            ("supply_pct", supplies, "supply_pct")):
        lo, hi = min(data[axis]), max(data[axis])
        bad_v = [v for v in vals if not _num(v) or not lo <= v <= hi]
        if bad_v:
            raise CheckError(f"corners.grid.{key} {bad_v} outside the "
                             f"corners.yaml range [{lo}, {hi}]")
    t_lo, t_hi = min(data["temperature_c"]), max(data["temperature_c"])
    if t_lo not in temps or t_hi not in temps:
        raise CheckError(f"corners.grid.temp_c must include {t_lo} and "
                         f"{t_hi}: a grid replaces the default five corners, "
                         "so it must still reach both temperature extremes")
    out = []
    for p in procs:
        for t in temps:
            for s in supplies:
                name = f"{'tt' if p == 'typical' else p}_{_tag(t)}c"
                if s != 0:
                    name += f"_v{'p' if s > 0 else ''}{_tag(s)}"
                out.append({"name": name, "process": p, "temp_c": t,
                            "supply_pct": s})
    return out


def spec_corners(data: dict, field="default", passives=(),
                 pinned=()) -> list[dict]:
    """The sweep a spec's `corners` field asks for, plus corners.yaml's
    passive_corners when `passives` (passive_devices of the design's
    netlist) is non-empty - brief: "Put passive-device spread into the
    corner grid for designs that use those devices". `pinned` names the
    scoped-out dimensions (recorded_scope_outs): each is held at typical
    at every corner, and a device that is pinned no longer brings the
    passive_corners in on its own."""
    out = _spec_corners(data, field)
    if [d for d in passives if d not in pinned]:
        have = {c["name"] for c in out}
        out += [dict(c) for c in data["passive_corners"]
                if c["name"] not in have]
    if pinned:
        for c in out:
            c["pinned"] = {d: "typical" for d in pinned}
    return out


# ---- scoped-out dimensions (the person's H1 ruling) ------------------------
SCOPE_CHECKPOINT = "H1"
# dimension -> what the person's words must name for it, and what it is
SCOPE_DIMENSIONS = {
    "mim_cap": {"names": re.compile(r"\bMIM\b", re.I),
                "what": "MIM capacitor corner (mimcap_<p>)"},
    "resistor": {"names": re.compile(r"\bresist", re.I),
                 "what": "poly/diffusion resistor corner (res_<p>)"},
}
SCOPE_OUT_RE = re.compile(r"out of scope|scoped? out|not in scope|"
                          r"pinned (?:at |to )?typical|not swept", re.I)
# where one ruling ends and the next begins, and a negation that turns the
# scope-out phrase after it into its opposite ("is not out of scope")
SCOPE_CLAUSE_RE = re.compile(r"[.;:!?\n]|\b(?:but|however|whereas|while|"
                             r"although|though|except)\b", re.I)
SCOPE_NEGATED_RE = re.compile(r"(?:\bnot|\bnever|\bno longer|n't)\s+"
                              r"(?:(?:be|been|being)\s+)?$", re.I)


def _norm(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def scope_out_problem(h1: dict | None, dimension: str,
                      quote: str) -> str | None:
    """Why the approved H1 record `h1` does not carry a scope-out of
    `dimension` in the words `quote`, or None when it does. Pure."""
    if dimension not in SCOPE_DIMENSIONS:
        return (f"unknown dimension {dimension!r}; a scope-out names one of "
                f"{sorted(SCOPE_DIMENSIONS)}")
    if not h1 or h1.get("status") != "approved":
        return (f"{SCOPE_CHECKPOINT} is not approved "
                f"(status {(h1 or {}).get('status')!r}): a scope-out is "
                f"taken only from the person's recorded {SCOPE_CHECKPOINT} "
                "answer")
    q = _norm(quote)
    if not q:
        return "--quote is empty"
    if q not in _norm(h1.get("answer")) and q not in _norm(h1.get("note")):
        return (f"the quote is not in the recorded {SCOPE_CHECKPOINT} answer "
                "or note: quote the person's own words verbatim")
    if not SCOPE_DIMENSIONS[dimension]["names"].search(quote):
        return (f"the quote does not name {dimension} "
                f"({SCOPE_DIMENSIONS[dimension]['what']}): the recorded "
                f"{SCOPE_CHECKPOINT} answer has to rule on that dimension "
                "itself")
    # The ruling has to sit in one clause that names this dimension and no
    # other, un-negated: "Resistor spread must be swept; MIM capacitor
    # spread is out of scope" rules out MIM only, and "MIM spread is not
    # out of scope" rules out nothing.
    others = [d for d in SCOPE_DIMENSIONS if d != dimension]
    shared = None
    for clause in SCOPE_CLAUSE_RE.split(quote):
        if not SCOPE_DIMENSIONS[dimension]["names"].search(clause):
            continue
        if not any(not SCOPE_NEGATED_RE.search(clause[:m.start()])
                   for m in SCOPE_OUT_RE.finditer(clause)):
            continue
        named = [d for d in others
                 if SCOPE_DIMENSIONS[d]["names"].search(clause)]
        if not named:
            return None
        shared = named
    if shared:
        return (f"the clause that rules {dimension} out also names "
                f"{', '.join(shared)}: a scope-out is taken only from a "
                "clause that rules on that one dimension")
    return ("the quote does not rule it out of scope (no un-negated 'out "
            "of scope', 'scoped out' or 'pinned typical' in a clause that "
            "names it)")


def recorded_scope_outs(state_data: dict) -> list[dict]:
    """The scope-outs state.json's approved H1 record carries, each
    re-verified against that record's own answer/note. A record that no
    longer verifies (a hand edit) is a CheckError, never a silent drop or
    a silent keep."""
    h1 = (state_data.get("human") or {}).get(SCOPE_CHECKPOINT) or {}
    out = []
    for s in h1.get("scope_out") or []:
        dim = (s or {}).get("dimension")
        problem = scope_out_problem(h1, dim, (s or {}).get("quote"))
        if problem:
            raise CheckError(
                f"state.json human.{SCOPE_CHECKPOINT}.scope_out {dim!r} does "
                f"not verify: {problem}. Re-record it with state.py "
                "scope-out from the person's answer, or remove it")
        out.append({"dimension": dim, "pinned": "typical",
                    "checkpoint": SCOPE_CHECKPOINT, "quote": s["quote"],
                    "what": SCOPE_DIMENSIONS[dim]["what"]})
    return out


def _spec_corners(data: dict, field) -> list[dict]:
    # brief: "Let a spec declare that grid, and keep the default five
    # corners when it doesn't."
    if field == "default":
        return default_corners(data)
    if field == "all":
        return grid_corners(data, {"process": list(data["process"]),
                                   "temp_c": list(data["temperature_c"]),
                                   "supply_pct": list(data["supply_pct"])})
    if isinstance(field, dict) and set(field) == {"grid"}:
        return grid_corners(data, field["grid"])
    if isinstance(field, list) and field and all(isinstance(c, str) for c in field):
        names = [c["name"] for c in data["default_corners"]]
        names += [c for c in field if c not in names]
        return corners_by_name(data, names)
    raise CheckError("spec.yaml 'corners' must be 'default', 'all', a "
                     "non-empty list of corner names, or {grid: {...}}")


def resolve_vdd(corner: dict, nominal_vdd: float) -> float:
    return nominal_vdd * (1.0 + corner["supply_pct"] / 100.0)


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corners-yaml", default=str(DEFAULT_YAML))
    ap.add_argument("--names", nargs="*", help="only these corner names, "
                    "in order (default: the full default_corners sweep)")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    data = load(args.corners_yaml)
    corners = (corners_by_name(data, args.names) if args.names
              else default_corners(data))
    payload = {"script": SCRIPT, "status": "pass",
              "corners_yaml": str(args.corners_yaml), "corners": corners}
    return payload, args.out


def main(argv=None) -> int:
    checklib.utf8_stdout()
    try:
        payload, out = run(argv)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}",
                          "remediation": str(exc)}))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
