#!/usr/bin/env python
"""corners.py - PVT corner expansion for /ade (docs/design.md 1.4, 5,
"### M8."). New at M8: no /hwde precedent (a PCB flow has no process
corner) - reads engine/reference/corners.yaml, the raw axes and the default
sweep (that file's own header explains the pairing choice).

    corners.py [--corners-yaml PATH] [--names N1 N2 ...] [--out FILE]

With no --names, prints the default corner set (`default_corners`). With
--names, prints exactly those corners by name, in the order given - a
block's spec.yaml `corners:` list (design.md 1.4) names a subset (or
superset, via add-corner) of this file's default_corners this way.

Library API (imported by sim_run.py / check_sim_pvt.py / check_bench_strength.py,
never re-implemented there):
    load(path) -> dict                       parsed + validated corners.yaml
    default_corners(data) -> list[dict]       the default sweep, validated
    corners_by_name(data, names) -> list[dict]
    resolve_vdd(corner, nominal_vdd) -> float  nominal * (1 + supply_pct/100)

Not a gate (no workspace, no violations) - a plain reference-data reader,
so its CLI contract is checklib's minus the pass/violations status: exit 0
on success, 2 on a bad corners.yaml or an unknown --names entry.
"""
from __future__ import annotations

import argparse
import json
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
    process_set = set(data["process"])
    temp_set = set(data["temperature_c"])
    supply_set = set(data["supply_pct"])
    seen_names: set[str] = set()
    for i, c in enumerate(defaults):
        if not isinstance(c, dict):
            raise CheckError(f"{p}: default_corners[{i}] is not a mapping")
        for key in ("name", "process", "temp_c", "supply_pct"):
            if key not in c:
                raise CheckError(f"{p}: default_corners[{i}] missing {key!r}")
        if c["name"] in seen_names:
            raise CheckError(f"{p}: duplicate corner name {c['name']!r}")
        seen_names.add(c["name"])
        if c["process"] not in process_set:
            raise CheckError(f"{p}: default_corners[{i}] process "
                             f"{c['process']!r} not in {sorted(process_set)}")
        if c["temp_c"] not in temp_set:
            raise CheckError(f"{p}: default_corners[{i}] temp_c "
                             f"{c['temp_c']!r} not in {sorted(temp_set)}")
        if c["supply_pct"] not in supply_set:
            raise CheckError(f"{p}: default_corners[{i}] supply_pct "
                             f"{c['supply_pct']!r} not in {sorted(supply_set)}")
    return data


def default_corners(data: dict) -> list[dict]:
    return [dict(c) for c in data["default_corners"]]


def corners_by_name(data: dict, names: list[str]) -> list[dict]:
    by_name = {c["name"]: c for c in data["default_corners"]}
    out = []
    for n in names:
        if n not in by_name:
            raise CheckError(f"unknown corner {n!r}; known: "
                             f"{sorted(by_name)}")
        out.append(dict(by_name[n]))
    return out


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
