"""rulingslib.py - a workspace's spec/mutant_rulings.yaml: the owner's
one-mutant-at-a-time rulings on mutation-gate survivors, read by /vde's
`mutate` (check_mutate.py) and /ade's `bench_strength`
(check_bench_strength.py).

There is no class-wide rule here and never should be: every entry names one
mutant id exactly as its gate reports it, who ruled and when, and why.

    equivalent:              # both gates
      - id: 12               # int for mutate, string for bench_strength
        ruling: "owner, 2026-09-25"
        evidence: "why no bench/test can observe it"
    below_spread:            # bench_strength only
      - id: xmpi_1_connection_removed
        netlist_line: "xmpi_1 n1 n3 p1 vdd pfet_03v3 ..."
        measure: f           # a tt measure the benches bound
        delta: 0.0051        # (mutant - baseline) / baseline, from the gate
        sigma: 0.0334        # relative 1-sigma of that measure, from mc
        ruling: "owner, 2026-09-25"
        evidence: "..."      # optional here
        sigmas: {v_out_low_v: 0.117}  # optional: the other measures' relative
                             # mc 1-sigma (absent: held to the 2% floor)

The file is optional; absent (or empty) means no rulings. This module only
checks the file's own shape - whether an entry matches what the gate
measured is the gate's job. Anything malformed is a CheckError (exit 2):
a ruling the gate cannot read is a refusal, never a silent pass.
"""
from __future__ import annotations

import math
from pathlib import Path

import yaml

from checklib import CheckError

RULINGS_REL = "spec/mutant_rulings.yaml"
GATES = ("mutate", "bench_strength")
FIELDS = {
    "equivalent": {"required": ("id", "ruling", "evidence"), "optional": ()},
    "below_spread": {"required": ("id", "netlist_line", "measure", "delta",
                                  "sigma", "ruling"),
                     "optional": ("evidence", "sigmas")},
}
LISTS_BY_GATE = {"mutate": ("equivalent",),
                 "bench_strength": ("equivalent", "below_spread")}
FIX = (f" - fix or remove the entry in {RULINGS_REL} (one entry per mutant, "
       "see engine/lib/rulingslib.py for the format)")


def _number(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v))


def _check_id(v, gate: str) -> bool:
    if gate == "mutate":
        return isinstance(v, int) and not isinstance(v, bool)
    return isinstance(v, str) and bool(v.strip())


def _check_field(key: str, v) -> bool:
    if key in ("ruling", "evidence", "netlist_line", "measure"):
        return isinstance(v, str) and bool(v.strip())
    if key == "delta":
        return _number(v)
    if key == "sigma":
        return _number(v) and v >= 0
    if key == "sigmas":
        return isinstance(v, dict) and bool(v) and all(
            isinstance(k, str) and k.strip() and _number(x) and x >= 0
            for k, x in v.items())
    return True


def load(ws: Path, gate: str) -> dict[str, dict]:
    """{list name: {id: entry}} for every list `gate` reads (each present,
    possibly empty). Raises CheckError on anything malformed."""
    if gate not in GATES:
        raise CheckError(f"rulingslib: unknown gate {gate!r}")
    allowed = LISTS_BY_GATE[gate]
    out: dict[str, dict] = {name: {} for name in allowed}
    path = Path(ws) / RULINGS_REL
    if not path.is_file():
        return out
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CheckError(f"{RULINGS_REL} is not valid YAML: {exc}"
                         + FIX) from exc
    if data is None:
        return out
    if not isinstance(data, dict):
        raise CheckError(f"{RULINGS_REL} must be a YAML mapping, got "
                         f"{type(data).__name__}" + FIX)
    for key in data:
        if key not in FIELDS:
            raise CheckError(f"{RULINGS_REL}: unknown top-level key {key!r} "
                             f"(expected {', '.join(FIELDS)})" + FIX)
        if key not in allowed:
            raise CheckError(f"{RULINGS_REL}: '{key}' does not apply to the "
                             f"{gate} gate (it reads only "
                             f"{', '.join(allowed)})" + FIX)
    seen: dict = {}
    for name in allowed:
        entries = data.get(name)
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise CheckError(f"{RULINGS_REL}: '{name}' must be a list of "
                             "entries" + FIX)
        spec = FIELDS[name]
        for i, entry in enumerate(entries):
            where = f"{RULINGS_REL} {name}[{i}]"
            if not isinstance(entry, dict):
                raise CheckError(f"{where}: must be a mapping" + FIX)
            unknown = set(entry) - set(spec["required"]) - set(spec["optional"])
            if unknown:
                raise CheckError(f"{where}: unknown field(s) "
                                 f"{', '.join(sorted(map(str, unknown)))}" + FIX)
            for field in spec["required"]:
                if field not in entry:
                    raise CheckError(f"{where}: missing required field "
                                     f"'{field}'" + FIX)
            for field in (*spec["required"], *spec["optional"]):
                if field not in entry:
                    continue
                ok = (_check_id(entry[field], gate) if field == "id"
                      else _check_field(field, entry[field]))
                if not ok:
                    want = ("an integer mcy mutant id" if gate == "mutate"
                            else "a non-empty mutant id string") \
                        if field == "id" else (
                            "a finite number" if field == "delta" else
                            "a finite number >= 0" if field == "sigma" else
                            "a non-empty map of measure -> finite number >= 0"
                            if field == "sigmas" else
                            "a non-empty string")
                    raise CheckError(f"{where}: '{field}' must be {want}, "
                                     f"got {entry[field]!r}" + FIX)
            mid = entry["id"]
            if mid in seen:
                if seen[mid] == name:
                    raise CheckError(f"{RULINGS_REL}: mutant {mid!r} is "
                                     f"listed twice under '{name}'" + FIX)
                raise CheckError(f"{RULINGS_REL}: mutant {mid!r} is listed "
                                 f"under both '{seen[mid]}' and '{name}'"
                                 + FIX)
            seen[mid] = name
            out[name][mid] = dict(entry)
    return out
