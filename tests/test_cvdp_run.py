"""evals/cvdp/run.py: the subset rule and the result shape, on synthetic
rows, with no download and no simulator (docs/design.md section 3, "### M6.")."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# Loaded under its own name: a bare `import run` could pick up another `run`.
_spec = importlib.util.spec_from_file_location(
    "cvdp_run", REPO / "evals" / "cvdp" / "run.py")
cvdp = importlib.util.module_from_spec(_spec)
sys.modules["cvdp_run"] = cvdp
_spec.loader.exec_module(cvdp)

COMPOSE = """services:
  direct:
    image: __OSS_SIM_IMAGE__
    env_file: ./src/.env
    command: pytest -s /src/test_runner.py
"""


def _row(pid="cvdp_copilot_x_0001", cat="cid003", compose=COMPOSE,
         env="SIM=icarus\n", runner="def test_x():\n    run_sim()\n"):
    return {"id": pid, "categories": [cat, "easy"],
            "input": {"context": {"rtl/x.sv": ""}},
            "output": {"context": {}},
            "harness": {"files": {"docker-compose.yml": compose,
                                  "src/.env": env,
                                  "src/test_runner.py": runner}}}


def test_a_plain_icarus_problem_is_in_and_yosys_does_not_exclude():
    assert cvdp.exclusion(_row()) is None
    assert cvdp.exclusion(_row(runner="def test_x():\n    yosys()\n")) is None


@pytest.mark.parametrize("row,why", [
    (_row(cat="cid012"), "category"),
    (_row(env="SIM=verilator\n"), "simulator"),
    (_row(runner="def test_x():\n    verilator()\n"), "verilator"),
    (_row(runner="def test_x():\n    xrun()\n"), "commercial-tool"),
    (_row(compose=COMPOSE.replace("__OSS_SIM_IMAGE__", "__VERIF_EDA_IMAGE__")),
     "image"),
    (_row(compose=COMPOSE.replace("pytest -s", "make && pytest -s")), "command"),
])
def test_each_exclusion_reason(row, why):
    assert cvdp.exclusion(row) == why


def test_a_dead_commercial_helper_does_not_exclude():
    runner = "def xrun_tb():\n    xrun()\n\ndef test_x():\n    run_sim()\n"
    assert cvdp.exclusion(_row(runner=runner)) is None


def test_select_orders_by_id_filters_and_limits():
    rows = [_row("p_0003"), _row("p_0001", cat="cid016"), _row("p_0002"),
            _row("p_0004", env="SIM=verilator\n")]
    chosen, excluded = cvdp.select(rows)
    assert [r["id"] for r in chosen] == ["p_0001", "p_0002", "p_0003"]
    assert excluded == {"p_0004": "simulator"}
    chosen, _ = cvdp.select(rows, categories=["cid003"], limit=1)
    assert [r["id"] for r in chosen] == ["p_0002"]


def test_aggregate_rates_by_category():
    recs = [{"category": "cid003", "status": "pass", "tests_ran": True},
            {"category": "cid003", "status": "fail", "tests_ran": True},
            {"category": "cid016", "status": "error", "tests_ran": False}]
    agg = cvdp.aggregate(recs)
    assert agg["overall"]["pass"] == 1 and agg["overall"]["total"] == 3
    assert agg["by_category"]["cid003"]["pass_rate"] == 0.5
    assert agg["by_category"]["cid016"]["pass"] == 0


def test_select_only_on_a_local_file_reports_subset_and_caveat(tmp_path, capsys):
    ds = tmp_path / "ds.jsonl"
    ds.write_text("\n".join(json.dumps(r) for r in
                            [_row("p_0001"), _row("p_0002", env="SIM=verilator\n")]))
    assert cvdp.main(["--dataset-file", str(ds), "--select-only"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["subset_size"] == 1 and out["ids"] == ["p_0001"]
    assert out["dataset"]["pinned"] is False
    assert "Not the official CVDP harness" in out["caveat"]


def test_a_file_that_is_not_json_is_an_error(tmp_path, capsys):
    ds = tmp_path / "ds.jsonl"
    ds.write_text("not json\n")
    assert cvdp.main(["--dataset-file", str(ds), "--select-only"]) == 2
    assert "remediation" in json.loads(capsys.readouterr().out)
