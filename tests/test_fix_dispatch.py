"""engine/scripts/fix_dispatch.py: routing + batching + parallel grouping,
domain swapped from PCB net/region to file-based clustering (docs/design.md
1.3)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))

import cluster_violations  # noqa: E402
import fix_dispatch  # noqa: E402


def _v(kind, file, domain=None, sev="error"):
    v = {"check": "board-review", "severity": sev, "file": file,
        "module": None, "line": 1, "refs": [], "msg": f"finding {kind}",
        "source": "review", "kind": kind}
    if domain:
        v["domain"] = domain
    return v


def test_fixer_domains_in_sync_with_dispatch():
    assert cluster_violations.FIXER_DOMAINS == frozenset(fix_dispatch.DOMAINS)


def test_end_to_end_domain_routed_orders(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    findings = {"gate": "lint", "phase": "P4", "violations": [
        _v("latch", "rtl/a.v", domain="rtl"),
        _v("weak-assert", "tb/a_tb.py", domain="testbench"),
        _v("mystery", "rtl/b.v", domain=None),
    ]}
    inp = tmp_path / "r.json"
    inp.write_text(json.dumps(findings), encoding="utf-8")
    payload, _ = fix_dispatch.run(["--input", str(inp), "--workspace", str(ws),
                                   "--out-dir", str(tmp_path / "wo")])
    by_fixer = {o["fixer"]: o for o in payload["orders"]}
    assert set(by_fixer) == {"rtl", "testbench", "review"}
    rtl_wo = json.loads(
        Path(by_fixer["rtl"]["work_order"]).read_text(encoding="utf-8"))
    assert "engine/scripts/gate.py" in rtl_wo["allowed_scripts"]
    assert rtl_wo["role_prompt"] is None    # no state.json -> no known skill


def test_orders_register_as_open_issues_when_state_given(tmp_path):
    sys.path.insert(0, str(ENGINE / "scripts"))
    import state as state_mod
    ws = tmp_path / "ws"
    state_mod.State.init(ws, "vde", "counter8")
    findings = {"gate": "lint", "violations": [_v("latch", "rtl/a.v", "rtl")]}
    inp = tmp_path / "r.json"
    inp.write_text(json.dumps(findings), encoding="utf-8")
    payload, _ = fix_dispatch.run(["--input", str(inp), "--workspace", str(ws)])
    assert payload["counts"]["orders"] == 1
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert len(data["open_issues"]) == 1
    assert data["open_issues"][0]["work_order"]
    wo = json.loads(Path(payload["orders"][0]["work_order"]).read_text())
    assert wo["role_prompt"] == "skills/vde/agents/fixer.md"


def test_merge_respects_cap():
    singles = [
        {"file": f"rtl/f{i}.v", "module": None, "kinds": [f"k{i}"],
         "checks": ["c"], "severity": "error", "count": 1,
         "fixer": "rtl",
         "violations": [{"severity": "error", "file": f"rtl/f{i}.v",
                         "kind": f"k{i}"}]}
        for i in range(12)
    ]
    merged = fix_dispatch.merge_small_clusters(singles)
    assert sum(c["count"] for c in merged) == 12
    assert all(c["count"] <= fix_dispatch.MERGE_CAP for c in merged)
    assert len(merged) == 2                      # 8 + 4


def test_large_clusters_pass_through_untouched():
    big = {"file": "rtl/a.v", "module": None, "kinds": ["latch"],
          "checks": ["c"], "severity": "error", "count": 5, "fixer": "rtl",
          "violations": [{"severity": "error", "file": "rtl/a.v",
                          "kind": "latch"} for _ in range(5)]}
    lone = {"file": None, "module": None, "kinds": ["x"], "checks": ["c"],
           "severity": "warning", "count": 1, "fixer": "review",
           "violations": [{"severity": "warning", "file": None, "kind": "x"}]}
    merged = fix_dispatch.merge_small_clusters([big, lone])
    assert big in merged and lone in merged and len(merged) == 2


def test_merged_order_severity_and_file():
    a = {"file": "rtl/a.v", "module": None, "kinds": ["a"], "checks": ["c"],
        "severity": "warning", "count": 1, "fixer": "rtl",
        "violations": [{"severity": "warning", "file": "rtl/a.v", "kind": "a"}]}
    b = {"file": "rtl/b.v", "module": None, "kinds": ["b"], "checks": ["c"],
        "severity": "error", "count": 1, "fixer": "rtl",
        "violations": [{"severity": "error", "file": "rtl/b.v", "kind": "b"}]}
    merged = fix_dispatch.merge_small_clusters([a, b])
    assert len(merged) == 1
    m = merged[0]
    assert m["severity"] == "error"              # max of the mergees
    assert m["file"] is None                      # files differ -> no single file
    assert m["merged_from"] == 2


def test_parallel_groups_split_by_disjoint_files():
    orders = [
        {"id": 1, "cluster": {"violations": [{"file": "rtl/a.v"}]}},
        {"id": 2, "cluster": {"violations": [{"file": "rtl/b.v"}]}},
        {"id": 3, "cluster": {"violations": [{"file": "rtl/a.v"}]}},  # shares a.v with #1
    ]
    groups = fix_dispatch.parallel_groups(orders)
    # #1 and #2 can share a group (disjoint files); #3 must be separate
    # from #1 (both touch rtl/a.v)
    flat = {i: g for g in groups for i in g}
    assert flat[1] != flat[3]
    total = sum(len(g) for g in groups)
    assert total == 3


def test_no_file_orders_serialize_alone():
    orders = [{"id": 1, "cluster": {"violations": [{"file": None}]}},
             {"id": 2, "cluster": {"violations": [{"file": None}]}}]
    groups = fix_dispatch.parallel_groups(orders)
    assert groups == [[1], [2]]
