"""engine/lib/statelib.py: normalizers, the invalidation map, two-layer gate
freshness. Hermetic (pure venv: yaml)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import statelib  # noqa: E402


def _h(tmp_path: Path, name: str, content: str, norm: str):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return statelib.hash_artifact(p, norm)


def test_text_eol_ignores_crlf_churn(tmp_path):
    a = _h(tmp_path, "a.v", "module x;\nendmodule\n", "text_eol")
    b = _h(tmp_path, "b.v", "module x;\r\nendmodule\r\n", "text_eol")
    assert a == b
    assert a.startswith("text_eol:")
    c = _h(tmp_path, "c.v", "module y;\nendmodule\n", "text_eol")
    assert c != a


def test_json_canonical_ignores_key_order_and_yaml_vs_json(tmp_path):
    a = _h(tmp_path, "a.json", '{"b": 1, "a": {"y": 2, "x": 3}}',
           "json_canonical")
    b = _h(tmp_path, "b.yaml", "a:\n  x: 3\n  y: 2\nb: 1\n", "json_canonical")
    assert a == b
    c = _h(tmp_path, "c.json", '{"b": 1, "a": {"y": 2, "x": 4}}',
           "json_canonical")
    assert c != a


def test_dir_text_hashes_every_file_name_sorted(tmp_path):
    d = tmp_path / "tb"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    (d / "b.py").write_text("y = 2\r\n", encoding="utf-8")
    h1 = statelib.hash_artifact(d, "dir_text")
    # CRLF churn inside a member file is invisible
    (d / "b.py").write_text("y = 2\n", encoding="utf-8")
    h2 = statelib.hash_artifact(d, "dir_text")
    assert h1 == h2
    (d / "c.py").write_text("z = 3\n", encoding="utf-8")
    h3 = statelib.hash_artifact(d, "dir_text")
    assert h3 != h1


def test_missing_file_hashes_none(tmp_path):
    assert statelib.hash_artifact(tmp_path / "nope.v", "text_eol") is None


def test_unparsable_json_falls_back_to_raw(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    h = statelib.hash_artifact(p, "json_canonical")
    assert h.startswith("raw:")


def test_norm_for_path(tmp_path):
    assert statelib.norm_for_path(tmp_path / "x.json") == "json_canonical"
    assert statelib.norm_for_path(tmp_path / "x.yaml") == "json_canonical"
    assert statelib.norm_for_path(tmp_path / "x.v") == "text_eol"
    (tmp_path / "d").mkdir()
    assert statelib.norm_for_path(tmp_path / "d") == "dir_text"


def test_load_map_is_valid_and_skill_scoped():
    imap = statelib.load_map()
    assert set(imap["gate_inputs"]) == set(statelib.SKILLS)
    assert set(imap["edit_classes"]) == set(statelib.SKILLS)
    assert "rtl_edit" in imap["edit_classes"]["vde"]
    assert "spec_lint" in imap["gate_inputs"]["vde"]


def test_load_map_rejects_unknown_skill(tmp_path):
    bad = tmp_path / "invalidation.yaml"
    bad.write_text("""
artifact_kinds:
  x: {path: "x", norm: text_eol}
gate_inputs:
  notaskill: {g: [x]}
edit_classes:
  vde: {}
""", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown skills"):
        statelib.load_map(bad)


def test_load_map_rejects_edit_class_gate_not_in_gate_inputs(tmp_path):
    bad = tmp_path / "invalidation.yaml"
    bad.write_text("""
artifact_kinds:
  x: {path: "x", norm: text_eol}
gate_inputs:
  vde: {g1: [x]}
edit_classes:
  vde:
    c1: {mutates: [x], stale_artifacts: [], gates: [g2], human_hold: 0}
""", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown gates"):
        statelib.load_map(bad)


def test_gate_freshness_two_layer():
    entry = {"last": {"inputs": {"rtl": "dir_text:aaa"}}}
    fresh = statelib.gate_freshness(entry, {"rtl": "dir_text:aaa"})
    assert fresh == {"hash_valid": True, "changed_inputs": [],
                     "stale_marks": [], "fresh": True}
    stale_hash = statelib.gate_freshness(entry, {"rtl": "dir_text:bbb"})
    assert stale_hash["hash_valid"] is False
    assert stale_hash["fresh"] is False
    marked = dict(entry, stale=[{"human_hold": 1}])
    stale_mark = statelib.gate_freshness(marked, {"rtl": "dir_text:aaa"})
    assert stale_mark["hash_valid"] is True
    assert stale_mark["fresh"] is False  # hash matches but a mark is present


def test_gate_freshness_unknown_gate_never_fresh():
    verdict = statelib.gate_freshness({}, {})
    assert verdict["hash_valid"] is None
    assert verdict["fresh"] is False


def test_kind_path_registry_override_requires_matching_kind():
    imap = statelib.load_map()
    # no override: the default template
    assert statelib.kind_path("rtl", imap, None) == "rtl"
    # an override registered under a DIFFERENT kind must not be honored
    reg = {"rtl": {"path": "somewhere/else", "kind": "tb"}}
    assert statelib.kind_path("rtl", imap, reg) == "rtl"
    # a matching-kind override IS honored
    reg2 = {"rtl": {"path": "somewhere/else", "kind": "rtl"}}
    assert statelib.kind_path("rtl", imap, reg2) == "somewhere/else"
