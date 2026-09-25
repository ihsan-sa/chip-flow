"""engine/lib/rulingslib.py: spec/mutant_rulings.yaml's own shape, the
owner's per-mutant rulings read by `mutate` and `bench_strength`. Fast."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "lib"))

import rulingslib  # noqa: E402
from checklib import CheckError  # noqa: E402

GOOD = """\
equivalent:
  - id: xmref_type_flipped
    ruling: "owner, 2026-09-25"
    evidence: "unobservable"
below_spread:
  - id: xmout_connection_removed
    netlist_line: "xmout a b c d nfet_03v3"
    measure: f
    delta: 0.005
    sigma: 0.03
    ruling: "owner, 2026-09-25"
"""


def ws_with(tmp_path: Path, text: str | None) -> Path:
    (tmp_path / "spec").mkdir()
    if text is not None:
        (tmp_path / "spec" / "mutant_rulings.yaml").write_text(
            text, encoding="utf-8")
    return tmp_path


def test_absent_or_empty_file_is_no_rulings(tmp_path):
    assert rulingslib.load(ws_with(tmp_path, None), "bench_strength") == {
        "equivalent": {}, "below_spread": {}}
    (tmp_path / "spec" / "mutant_rulings.yaml").write_text("",
                                                           encoding="utf-8")
    assert rulingslib.load(tmp_path, "mutate") == {"equivalent": {}}


def test_a_good_file_loads_by_id(tmp_path):
    r = rulingslib.load(ws_with(tmp_path, GOOD), "bench_strength")
    assert set(r["equivalent"]) == {"xmref_type_flipped"}
    assert r["below_spread"]["xmout_connection_removed"]["sigma"] == 0.03


@pytest.mark.parametrize("text, gate, match", [
    ("classes:\n  - x\n", "mutate", "unknown top-level key"),
    (GOOD, "mutate", "does not apply to the mutate gate"),
    (GOOD.replace('    evidence: "unobservable"\n', ""), "bench_strength",
     "missing required field 'evidence'"),
    (GOOD.replace('"owner, 2026-09-25"', '""', 1), "bench_strength",
     "'ruling' must be"),
    (GOOD.replace("delta: 0.005", "delta: big"), "bench_strength",
     "'delta' must be"),
    (GOOD.replace("sigma: 0.03", "sigma: -1"), "bench_strength",
     "'sigma' must be"),
    (GOOD.replace("sigma: 0.03", "sigma: 0.03\n    sigmas: {f: -1}"),
     "bench_strength", "'sigmas' must be"),
    (GOOD.replace("sigma: 0.03", "sigma: 0.03\n    sigmas: {}"),
     "bench_strength", "'sigmas' must be"),
    (GOOD.replace("xmout_connection_removed", "xmref_type_flipped"),
     "bench_strength", "under both"),
    (GOOD.split("below_spread")[0] + GOOD.split("below_spread")[0]
     .replace("equivalent:\n", ""), "bench_strength", "listed twice"),
    ("equivalent:\n  - id: \"12\"\n    ruling: r\n    evidence: e\n",
     "mutate", "'id' must be an integer"),
    ("equivalent:\n  - id: 12\n    ruling: r\n    evidence: e\n    by: x\n",
     "mutate", "unknown field"),
])
def test_malformed_rulings_are_refused(tmp_path, text, gate, match):
    with pytest.raises(CheckError, match=match):
        rulingslib.load(ws_with(tmp_path, text), gate)
