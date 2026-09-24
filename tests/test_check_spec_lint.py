"""engine/scripts/check_spec_lint.py: the CLI/report contract around
speclib.lint_spec (docs/design.md 1.1's script contract, 1.5's spec_lint
row)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_spec_lint  # noqa: E402

GOOD_YAML = """
top: counter8
requirements:
  - id: REQ-WRAP
    text: count wraps 15 -> 0
    check: sim
clock:
  period_ns: 10
  domains: [clk]
"""

BAD_YAML = """
top: counter8
requirements:
  - id: REQ-WRAP
    text: count wraps 15 -> 0
"""


def make_ws(tmp_path: Path, spec_text: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(spec_text, encoding="utf-8")
    return ws


def test_clean_spec_passes(tmp_path, capsys):
    ws = make_ws(tmp_path, GOOD_YAML)
    code = check_spec_lint.main(["--workspace", str(ws)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pass"
    assert out["violations"] == []
    assert out["report_schema"] == 1


def test_bad_spec_fails_with_the_named_fault(tmp_path, capsys):
    ws = make_ws(tmp_path, BAD_YAML)
    code = check_spec_lint.main(["--workspace", str(ws)])
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "violations"
    kinds = {v["kind"] for v in out["violations"]}
    assert "requirement_no_check" in kinds


def test_missing_spec_yaml_is_an_error(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    code = check_spec_lint.main(["--workspace", str(ws)])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert out["remediation"]


def test_out_file_written(tmp_path):
    ws = make_ws(tmp_path, GOOD_YAML)
    out_path = tmp_path / "result.json"
    code = check_spec_lint.main(["--workspace", str(ws), "--out", str(out_path)])
    assert code == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["status"] == "pass"
