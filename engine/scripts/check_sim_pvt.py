#!/usr/bin/env python
"""check_sim_pvt.py - the sim_pvt gate (docs/design.md 1.5, "### M8."):
sim_run.py over the block's corner set (design.md 5: "the default is the
four extremes with typical, never fewer").

    check_sim_pvt.py --workspace DIR [--out FILE]

Pass criteria (gates.yaml): "Every measure inside its bound at every
corner." Fault this gate must catch: "meets at typical, loses headroom at
slow and hot" - engine/reference/corners.yaml's own `ss` default corner
(125 C, -10% supply) is exactly that combination.

Corner set: spec.yaml's top-level `corners` field (speclib.lint_spec_ade's
schema: "default" | "all" | a list of corner names) - "default" (the
implicit value when the field is absent) is corners.py's own
default_corners(). A list is UNIONED with the default five, never used to
replace them (corner_names_for_spec()) - design.md 5's "never fewer" means
what it says, so a spec cannot skip a default corner (say, drop `ss` and
have 'meets at typical, loses headroom at slow and hot' go uncaught) by
simply not naming it; a list only ever adds names on top of the default
five (add-corner - skills/ade/reference/tasks.yaml - is how a spec asks for
more).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import corners as corners_mod  # noqa: E402
import sim_run  # noqa: E402
import speclib  # noqa: E402

SCRIPT = "check_sim_pvt"
DEFAULT_TIMEOUT = 60.0


def corner_names_for_spec(spec: dict, default_names: list[str]) -> list[str] | None:
    """None means "sim_run's own default" (corners.py's default_corners(),
    design.md 5's five curated points) - the common case, and the cheapest:
    sim_run re-derives the same set itself.

    A list is add-corner's own widening (skills/ade/reference/tasks.yaml:
    "this verb is how a spec asks for more"), never a replacement - design.md
    5 is explicit that the default five are "never fewer", so an explicit
    list is UNIONED with `default_names`, defaults first in their own order
    then any extra names the spec added, rather than passed through as-is.
    A spec naming only `[tt]` used to run just tt and silently never catch
    'meets at typical, loses headroom at slow and hot' at ss - that is
    exactly the fault this gate exists to catch, so a spec cannot opt out of
    it by naming a narrower corner list."""
    field = spec.get("corners", "default")
    if field == "default":
        return None
    names = list(default_names)
    for c in field:
        if c not in names:
            names.append(c)
    return names


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    default_names = [c["name"] for c in
                     corners_mod.default_corners(corners_mod.load())]
    corners_field = corner_names_for_spec(spec, default_names)

    result = sim_run.run_workspace_benches(
        ws, corner_names=corners_field, timeout=args.timeout, check="sim_pvt")
    top, corner_list = result["top"], result["corners"]
    results, violations = result["results"], result["violations"]

    # stamp() hashes exactly this path as input_digest; gate.py's own
    # record_gate cross-checks that against invalidation.yaml's gate_inputs
    # kinds[0] for this gate ("netlist" for sim_pvt).
    payload = checklib.report(SCRIPT, ws / "netlist", violations, top=top,
                              corners=corner_list, results=results)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
