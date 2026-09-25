"""cluster_violations.py - group open findings for fixer dispatch.

Ported from /hwde's scripts/cluster_violations.py, domain swapped (docs/
design.md 1.3): "the clustering key becomes file, module and finding kind" -
a PCB finding sits at a board (x, y) and clusters by spatial radius; a
chip-flow finding sits in a text artifact (an RTL file, a SPICE netlist, a
spec.yaml requirement) with no geometry, so clustering groups by the triple
(file, module, kind) directly - no spatial union-find, no bbox/region.

CLI: --input report.json  (any report with a `violations` list)
     [--out clusters.json]
Exit: 0 no clusters, 1 clusters present (work to do), 2 error.

Cluster schema:
    {"id", "file", "module", "kinds"[], "checks"[], "severity", "count",
     "fixer": "<domain>", "violations": [ ...the raw findings... ]}
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import checklib  # noqa: E402

SCRIPT = "cluster_violations"
SEV_RANK = {"error": 2, "warning": 1, "info": 0}

# The fixer domains fix_dispatch.py defines (kept in sync by
# tests/test_fix_dispatch.py; not imported - fix_dispatch imports this
# module). A finding carrying an explicit "domain" (a reviewer agent
# transcribes one per finding) routes there directly - reviewer kinds are
# free slugs FIXER_HINTS cannot enumerate.
FIXER_DOMAINS = frozenset({
    "rtl", "testbench", "formal", "synth", "harden", "layout", "sizing",
    "netlist", "review"})

# kind -> the fixer domain best suited to resolve it. Empty at M1 (every
# gate is a stub, so no real finding kind exists yet); each milestone that
# lands a real check_<gate>.py adds its finding kinds here as it learns
# them (docs/design.md 1.5's "planted fault" column is the source for each
# one). A finding without a matching kind falls back to its `check` name,
# then to 'review'.
#
# vde's own kinds land at M5 (docs/design.md, "### M5."): every kind M2/M3's
# check_<gate>.py scripts actually emit (grep checklib.violation( call sites
# under engine/scripts/check_*.py) gets a routing rule here, so the fix loop
# (skills/vde/SKILL.md) dispatches to the right fixer domain without a human
# in the loop for the common case. The judgment calls, spelled out once:
#   - `sim`/`holdout` test FAILURES read as the DESIGN being wrong against a
#     testbench written first, in fresh context, from the spec alone (docs/
#     design.md section 2) - they route to "rtl". A test that never ran
#     (skipped) or a requirement with no test tagged to it is a TESTBENCH
#     gap - "testbench".
#   - `mutate` scores the testbench, never the design ("a mutate failure
#     goes back to the tb-writer, not the rtl-writer", docs/design.md
#     section 2) - every survivor_* class and the kill-rate rollup route to
#     "testbench".
#   - `formal` property failures are exactly what formal exists to catch
#     that sim cannot ("formal is the answer to tests that pass too
#     easily") - "rtl". A second-engine disagreement or an unreached cover
#     point is a property-writer/property concern, not a design one -
#     "formal" (whose own guidance in fix_dispatch.DOMAINS already says
#     "widen the induction depth or fix the property, never loosen it").
#   - `cover` gaps are what the tests never reached - "testbench", matching
#     "Coverage says what the tests never reached" (docs/design.md
#     section 2).
#   - `synth` findings (latch, unmapped cell, combinational loop, undriven
#     net) are "almost always an RTL defect surfacing late" (fix_dispatch's
#     own synth-domain guidance) - "rtl".
#   - `lint` findings are RTL text defects - "rtl"; an unrecognized
#     verilator rule (a kind this table has never seen) still falls back to
#     "review" by design, same as any other unmapped kind.
#   - `release`'s own `gate_not_ready` is attest.py's coverage refusal, not
#     a design defect - "review" (the human/orchestrator reads which gate
#     is missing and re-enters the right phase; no script fixes this).
#   - `harden`/`timing`/`drc`/`lvs`/`glsim`/`precheck` (M4, docs/design.md
#     "### M4.") all findings on the ALREADY-hardened design (a LibreLane
#     flow step failing outright, a timing corner in violation, a DRC/LVS
#     mismatch, precheck) - "harden", matching fix_dispatch.DOMAINS'
#     existing "harden" guidance (set keys in harden/config.override.json
#     or fix the RTL, then re-run the harden job); never routed straight to "rtl" the way
#     synth's own findings are, because the fix is as often a harden
#     constraint (floorplan, clock period, pin order) as it is the design.
#     glsim's own `test_failed`/`test_skipped` are the one exception -
#     shared with sim/holdout above, since a functional bug that only
#     shows up gate-level is still a design defect ("harden" is silent
#     bystander there, not the domain to route it to).
FIXER_HINTS: dict[str, str] = {
    # lint (engine/scripts/check_lint.py) - verilator rule kinds, plus the
    # rule-less fallbacks the script itself uses.
    "compile_error": "rtl",
    "warning": "rtl",
    "LATCH": "rtl",
    "CASEINCOMPLETE": "rtl",
    "CASEOVERLAP": "rtl",
    "CASEX": "rtl",
    "WIDTH": "rtl",
    "WIDTHEXPAND": "rtl",
    "WIDTHTRUNC": "rtl",
    "UNUSEDSIGNAL": "rtl",
    "UNDRIVEN": "rtl",
    "MULTIDRIVEN": "rtl",
    "BLKSEQ": "rtl",
    "COMBDLY": "rtl",
    "SYNCASYNCNET": "rtl",

    # sim (check_sim.py)
    "test_failed": "rtl",
    "test_skipped": "testbench",
    "requirement_no_test": "testbench",

    # holdout (check_holdout.py) - "test_skipped" is shared with sim above.
    "holdout_failed": "rtl",
    "untagged_holdout_test": "testbench",

    # mutate (check_mutate.py) - every mutate_runner.classify() class.
    "kill_rate_below_threshold": "testbench",
    "survivor_reset_removed": "testbench",
    "survivor_output_stuck": "testbench",
    "survivor_condition_inverted": "testbench",
    "survivor_stuck_other": "testbench",
    "survivor_conditional_stuck": "testbench",
    "survivor_other": "testbench",

    # formal (check_formal.py)
    "property_failed": "rtl",
    "engine_disagreement": "formal",
    "bounded_not_proven": "formal",
    "cover_not_reached": "formal",

    # cover (check_cover.py)
    "line_coverage_below_threshold": "testbench",
    "toggle_coverage_below_threshold": "testbench",
    "line_not_covered": "testbench",
    "toggle_not_covered": "testbench",

    # synth (check_synth.py)
    "combinational_loop": "rtl",
    "no_driver": "rtl",
    "unmapped_cell": "rtl",
    "latch": "rtl",

    # release (check_release.py, via attest.py build())
    "gate_not_ready": "review",

    # harden (check_harden.py)
    "flow_step_failed": "harden",
    "harden_missing_artifact": "harden",

    # timing (check_timing.py)
    "setup_violation": "harden",
    "hold_violation": "harden",
    "slew_or_cap_or_fanout_violation": "harden",

    # drc (check_drc.py)
    "magic_drc_violation": "harden",
    "klayout_drc_violation": "harden",

    # lvs (check_lvs.py)
    "netlist_mismatch": "harden",

    # precheck (check_precheck.py) - glsim's own kinds (test_failed/
    # test_skipped) are shared with sim/holdout above.
    "precheck_failed": "harden",

    # ---- /ade (M8/M9; skills/ade/SKILL.md's fix loop). The judgment calls:
    #   - netlist_lint findings are the netlist's own text (a model the PDK
    #     lacks, a declared device missing, a floating node) - "netlist".
    #   - a sim_tt/sim_pvt/mc bound miss reads as the DESIGN being wrong
    #     against a bench written first from the spec (the same call sim's
    #     test_failed makes for vde) - "sizing", whose guidance lets the
    #     fixer move to the netlist when sizing alone cannot close it.
    #   - bench_strength scores the BENCH, never the design - every survivor
    #     class routes to "testbench" (the bench-writer), exactly as mutate's
    #     survivors do.
    #   - drc/lvs/pex_sim are judgments on the generated layout - "layout"
    #     (the layout-fixer edits layout/gen_<block>.py, never the GDS). DRC
    #     kinds are klayout rule names (M1.2a, DF.14_LV, metal1_OFFGRID, ...)
    #     no table can enumerate; CHECK_HINTS below routes them by gate.
    #   - spec_lint kinds stay unmapped ("review") on purpose: the spec-writer
    #     fixes those inside the spec/full-run recipes, same as vde's
    #     requirement_no_check.

    # netlist_lint (check_netlist_lint.py); its dry run's sim_engine_error_*
    # kinds route through KIND_PREFIX_HINTS below.
    "model_not_in_pdk": "netlist",
    "declared_device_missing": "netlist",
    "floating_node": "netlist",

    # sim_tt / sim_pvt (simlib.compare_bounds) and mc (check_mc.py)
    "sim_bound_fail": "sizing",
    "sim_measure_missing": "testbench",
    "yield_below_spec": "sizing",
    "yield_all_failed": "sizing",

    # bench_strength (check_bench_strength.py, netlistlib.device_mutants)
    "survivor_size_doubled": "testbench",
    "survivor_connection_removed": "testbench",
    "survivor_type_flipped": "testbench",
    "survivor_bias_halved": "testbench",

    # lvs (check_analog_lvs.py) and pex_sim (check_pex_sim.py) - a pex
    # bench that never printed a value is the bench-writer's, not the layout's
    "lvs_mismatch": "layout",
    "measure_out_of_bounds": "layout",
    "measure_missing": "testbench",
}

# Kinds built at run time from a prefix (simlib.engine_error_violations:
# `sim_engine_error_<pattern>`, one per ngspice failure signature) - a
# convergence failure, an unknown subckt or node is the netlist's (or the
# bench's instantiation of it) to fix, never a bound to widen.
KIND_PREFIX_HINTS: tuple[tuple[str, str], ...] = (
    ("sim_engine_error_", "netlist"),
)

# Last resort before 'review', keyed by the finding's own `check` (the gate's
# tool name): a gate whose kinds are an open set (klayout DRC rule names)
# still routes to the one domain that can fix anything it reports.
CHECK_HINTS: dict[str, str] = {
    "analog_drc": "layout",
    "analog_lvs": "layout",
    "pex_sim": "layout",
}


def fixer_for(kind: str | None, checks=()) -> str:
    """The fixer domain for one kind: FIXER_HINTS, then a KIND_PREFIX_HINTS
    prefix, then CHECK_HINTS when every finding shares one check, else
    'review'."""
    if kind in FIXER_HINTS:
        return FIXER_HINTS[kind]
    for prefix, domain in KIND_PREFIX_HINTS:
        if kind and kind.startswith(prefix):
            return domain
    checks = {c for c in checks if c}
    if len(checks) == 1:
        return CHECK_HINTS.get(checks.pop(), "review")
    return "review"


def _uf_find(parent, i):
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def kind_of(v: dict) -> str | None:
    """A finding's dispatch kind: explicit `kind`, else its `check` name."""
    return v.get("kind") or v.get("check")


def cluster(violations: list[dict], *_ignored) -> list[dict]:
    """Group by (file, module, kind) - the whole clustering key (docs/
    design.md 1.3). Extra positional args (radius) are accepted and ignored
    so callers written against the PCB-era spatial signature still work."""
    by_key: dict[tuple, list] = {}
    for v in violations:
        key = (v.get("file"), v.get("module"), kind_of(v))
        by_key.setdefault(key, []).append(v)
    clusters: list[dict] = []
    for (file, module, kind), group in by_key.items():
        sev = max((g.get("severity", "info") for g in group),
                  key=lambda s: SEV_RANK.get(s, 0))
        kinds = sorted({k for g in group if (k := kind_of(g))})
        checks = sorted({c for g in group
                         if (c := g.get("source") or g.get("check"))})
        # explicit per-finding domain wins when the group agrees on exactly
        # one valid name; else the kind-keyed hint table
        doms = {d for g in group if (d := g.get("domain")) in FIXER_DOMAINS}
        fixer = doms.pop() if len(doms) == 1 else fixer_for(
            kind, (g.get("check") for g in group))
        clusters.append({
            "file": file, "module": module, "kinds": kinds, "checks": checks,
            "severity": sev, "count": len(group), "fixer": fixer,
            "violations": group,
        })
    clusters.sort(key=lambda c: (-SEV_RANK.get(c["severity"], 0), -c["count"]))
    for i, c in enumerate(clusters):
        c["id"] = i
    return clusters


def load_violations(path) -> list[dict]:
    data = checklib.load_json(path, "input report")
    if isinstance(data, list):
        return data
    return data.get("violations", [])


def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Cluster open findings by (file, module, kind).")
    ap.add_argument("--input", required=True,
                    help="a check_<gate>.py report or any report with "
                         "a violations list")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    violations = load_violations(args.input)
    clusters = cluster(violations)
    by_sev: dict[str, int] = {}
    for c in clusters:
        by_sev[c["severity"]] = by_sev.get(c["severity"], 0) + 1
    payload = {
        "script": SCRIPT,
        "status": "violations" if clusters else "pass",
        "counts": {"clusters": len(clusters), "violations": len(violations),
                   "by_severity": by_sev},
        "clusters": clusters,
    }
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
