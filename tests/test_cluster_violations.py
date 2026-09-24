"""engine/scripts/cluster_violations.py: groups by (file, module, kind) -
docs/design.md 1.3: "the clustering key becomes file, module and finding
kind" (no spatial position/region - that was PCB-only)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))

import cluster_violations  # noqa: E402


def _v(kind, file="rtl/counter8.v", module="counter8", domain=None,
      sev="error"):
    v = {"check": "check_lint", "severity": sev, "file": file,
        "module": module, "line": 1, "kind": kind, "refs": [],
        "msg": f"finding {kind}", "source": "check_lint"}
    if domain:
        v["domain"] = domain
    return v


def test_groups_by_file_module_kind():
    clusters = cluster_violations.cluster(
        [_v("latch"), _v("latch"), _v("unmapped_cell")])
    assert len(clusters) == 2
    by_kind = {c["kinds"][0]: c for c in clusters}
    assert by_kind["latch"]["count"] == 2
    assert by_kind["unmapped_cell"]["count"] == 1


def test_different_files_never_merge():
    clusters = cluster_violations.cluster(
        [_v("latch", file="rtl/a.v"), _v("latch", file="rtl/b.v")])
    assert len(clusters) == 2


def test_explicit_domain_wins():
    clusters = cluster_violations.cluster([_v("odd-thing", domain="synth")])
    assert clusters[0]["fixer"] == "synth"


def test_invalid_domain_falls_back_to_hints_then_review():
    clusters = cluster_violations.cluster([_v("odd-thing", domain="wizardry")])
    assert clusters[0]["fixer"] == "review"


def test_fixer_domains_matches_the_hint_table_universe():
    assert cluster_violations.FIXER_DOMAINS >= set(cluster_violations.FIXER_HINTS.values())


def test_cluster_accepts_and_ignores_legacy_radius_arg():
    # a caller written against the PCB-era cluster(violations, radius)
    # signature still works - the radius is accepted and ignored.
    clusters = cluster_violations.cluster([_v("latch")], 5.0)
    assert len(clusters) == 1


def test_run_cli_exit_status(tmp_path):
    import json
    inp = tmp_path / "r.json"
    inp.write_text(json.dumps({"violations": [_v("latch")]}), encoding="utf-8")
    payload, _out = cluster_violations.run(["--input", str(inp)])
    assert payload["status"] == "violations"
    assert payload["counts"]["clusters"] == 1

    empty = tmp_path / "e.json"
    empty.write_text(json.dumps({"violations": []}), encoding="utf-8")
    payload2, _ = cluster_violations.run(["--input", str(empty)])
    assert payload2["status"] == "pass"
