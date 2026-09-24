"""engine/scripts/check_spec_lint_ade.py: the CLI/report contract around
speclib.lint_spec_ade plus this script's own tb/*.bounds.json reconciliation
(docs/design.md 1.1's script contract, 1.5's ade spec_lint row). Hermetic:
no eda/ngspice call, tb/*.bounds.json is read straight off disk."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_spec_lint_ade  # noqa: E402

GOOD_YAML = """\
top: current_mirror
supply: {vdd: 3.3}
devices: [xmref, xmout]
corners: default
measures:
  - name: iout_ratio
    bounds: {min: 1.8, max: 2.2}
"""


def make_ws(tmp_path: Path, spec_text: str = GOOD_YAML) -> Path:
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(spec_text, encoding="utf-8")
    return ws


def write_bounds(ws: Path, bench_stem: str, entries: list[dict]) -> None:
    (ws / "tb").mkdir(parents=True, exist_ok=True)
    (ws / "tb" / f"{bench_stem}.cir").write_text("* bench\n", encoding="utf-8")
    (ws / "tb" / f"{bench_stem}.bounds.json").write_text(
        json.dumps(entries), encoding="utf-8")


def test_clean_spec_with_no_tb_yet_passes(tmp_path, capsys):
    # right after spec-writer runs, before any bench exists - nothing to
    # reconcile, and no error either.
    ws = make_ws(tmp_path)
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pass"


def test_matching_bench_bounds_passes(tmp_path, capsys):
    ws = make_ws(tmp_path)
    write_bounds(ws, "mirror_tb", [{"measure": "iout_ratio", "min": 1.8, "max": 2.2}])
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pass"


def test_spec_measure_with_no_bench_bound_fails(tmp_path, capsys):
    ws = make_ws(tmp_path)
    write_bounds(ws, "mirror_tb", [{"measure": "some_other_measure", "min": 0, "max": 1}])
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    kinds = {v["kind"] for v in out["violations"]}
    assert "measure_no_bench_bound" in kinds


def test_bench_bound_with_no_spec_measure_fails(tmp_path, capsys):
    ws = make_ws(tmp_path)
    write_bounds(ws, "mirror_tb", [
        {"measure": "iout_ratio", "min": 1.8, "max": 2.2},
        {"measure": "extra_measure", "min": 0, "max": 1},
    ])
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    kinds = {v["kind"] for v in out["violations"]}
    assert "bench_bound_no_spec_measure" in kinds


def test_missing_spec_yaml_is_an_error(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
