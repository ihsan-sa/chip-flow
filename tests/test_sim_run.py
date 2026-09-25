"""engine/scripts/sim_run.py: ngspice bench running at one or more PVT
corners (docs/design.md 1.3, 1.5, "### M8."). Fast tests fake `eda` itself
(a tiny script printing canned ngspice-shaped text) so corner/sizing
plumbing is exercised with no real ngspice call; `slow` tests run the REAL
gf180mcuD toolchain end to end - see test_check_sim_tt.py /
test_check_sim_pvt.py for the gate-level real-tool proofs this milestone's
brief asks for ("prove with a test that an ngspice run that errors, fails
to converge, or produces a measure it never printed fails the gate")."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import sim_run  # noqa: E402
from checklib import CheckError  # noqa: E402

SPEC_YAML = """\
top: widget
supply: {vdd: 3.3}
devices: [xm1]
requirements: []
measures:
  - {name: vout, bounds: {min: 1.0, max: 1.5}, corners: default}
"""

BENCH_TEMPLATE = """\
.include '{{PDK}}/libs.tech/ngspice/design.spice'
.lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' {{CORNER}}
.temp {{TEMP_C}}
.include '{{NETLIST}}'
{{SIZING}}
vdd vdd 0 {{VDD}}
.control
op
print v(vdd)
.endc
.end
"""

BOUNDS = json.dumps([{"measure": "vout", "min": 1.0, "max": 1.5}])


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb", "sizing"):
        (ws / sub).mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(SPEC_YAML, encoding="utf-8")
    (ws / "netlist" / "widget.cir").write_text("* netlist\n", encoding="utf-8")
    (ws / "tb" / "widget_tb.cir").write_text(BENCH_TEMPLATE, encoding="utf-8")
    (ws / "tb" / "widget_tb.bounds.json").write_text(BOUNDS, encoding="utf-8")
    return ws


def make_fake_eda(tmp_path: Path, stdout: str, stderr: str = "",
                  exit_code: int = 0) -> Path:
    """A stand-in for bin/eda that ignores its arguments (including
    `--print-toolchain-root`, answered with a fake toolchain dir so
    pdk_root() resolves) and prints canned text - proves sim_run's own
    plumbing (deck materialization, corner/sizing substitution, bounds
    scoring) independent of a real ngspice call."""
    fake_root = tmp_path / "fake_toolchain"
    (fake_root / "foss" / "pdks" / "gf180mcuD").mkdir(parents=True)
    script = tmp_path / "fake_eda"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --print-toolchain-root ]; then\n"
        f"  echo '{fake_root}'\n"
        "  exit 0\n"
        "fi\n"
        f"cat >&2 <<'STDERR'\n{stderr}\nSTDERR\n"
        f"cat <<'STDOUT'\n{stdout}\nSTDOUT\n"
        f"exit {exit_code}\n",
        encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


CLEAN_MEASURE_STDOUT = """\

  Measurements for Transient Analysis

vout                 =  1.20000e+00
"""


def test_run_workspace_benches_clean_pass(tmp_path):
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, CLEAN_MEASURE_STDOUT)
    result = sim_run.run_workspace_benches(ws, eda_bin=eda, corner_names=["tt"])
    assert result["violations"] == []
    assert result["results"][0]["measures"]["vout"] == pytest.approx(1.2)


def test_run_workspace_benches_out_of_bounds_fails(tmp_path):
    ws = make_ws(tmp_path)
    bad_stdout = CLEAN_MEASURE_STDOUT.replace("1.20000e+00", "9.90000e+00")
    eda = make_fake_eda(tmp_path, bad_stdout)
    result = sim_run.run_workspace_benches(ws, eda_bin=eda, corner_names=["tt"])
    kinds = {v["kind"] for v in result["violations"]}
    assert "sim_bound_fail" in kinds


def test_run_workspace_benches_never_printed_measure_fails(tmp_path):
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, "\n  Measurements for Transient Analysis\n")
    result = sim_run.run_workspace_benches(ws, eda_bin=eda, corner_names=["tt"])
    kinds = {v["kind"] for v in result["violations"]}
    assert "sim_measure_missing" in kinds


def test_run_workspace_benches_engine_error_fails_even_with_clean_measures(tmp_path):
    # the exact case this milestone's brief calls out: a run that "fails to
    # converge" but still prints every requested measure must still fail.
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, CLEAN_MEASURE_STDOUT,
                        stderr="Warning: singular matrix:  check node x\n")
    result = sim_run.run_workspace_benches(ws, eda_bin=eda, corner_names=["tt"])
    kinds = {v["kind"] for v in result["violations"]}
    assert "sim_engine_error_singular_matrix" in kinds


def test_run_workspace_benches_nonzero_exit_with_no_measures_fails(tmp_path):
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, "", stderr="Error: unknown subckt\n",
                        exit_code=1)
    result = sim_run.run_workspace_benches(ws, eda_bin=eda, corner_names=["tt"])
    kinds = {v["kind"] for v in result["violations"]}
    assert "sim_measure_missing" in kinds or "sim_engine_error_unknown_subckt" in kinds


def test_run_workspace_benches_sweeps_default_corners(tmp_path):
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, CLEAN_MEASURE_STDOUT)
    result = sim_run.run_workspace_benches(ws, eda_bin=eda)  # no corner_names
    names = {r["corner"] for r in result["results"]}
    assert names == {"tt", "ss", "ff", "sf", "fs"}


def test_deck_actually_contains_the_requested_corner_and_temp(tmp_path):
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, CLEAN_MEASURE_STDOUT)
    result = sim_run.run_workspace_benches(ws, eda_bin=eda, corner_names=["ss"])
    deck_text = Path(result["results"][0]["deck"]).read_text(encoding="utf-8")
    assert "sm141064.spice' ss" in deck_text
    assert ".temp 125" in deck_text
    assert "vdd vdd 0 2.97" in deck_text  # 3.3 * 0.9


def test_missing_supply_vdd_raises(tmp_path):
    ws = make_ws(tmp_path)
    (ws / "spec" / "spec.yaml").write_text("top: widget\nrequirements: []\n",
                                           encoding="utf-8")
    eda = make_fake_eda(tmp_path, CLEAN_MEASURE_STDOUT)
    with pytest.raises(CheckError):
        sim_run.run_workspace_benches(ws, eda_bin=eda, corner_names=["tt"])


def test_no_bench_with_bounds_sidecar_raises(tmp_path):
    ws = make_ws(tmp_path)
    (ws / "tb" / "widget_tb.bounds.json").unlink()
    eda = make_fake_eda(tmp_path, CLEAN_MEASURE_STDOUT)
    with pytest.raises(CheckError):
        sim_run.run_workspace_benches(ws, eda_bin=eda, corner_names=["tt"])


# ---------------------------------------------------------------------- CLI

def test_cli_reports_pass_as_json(tmp_path, capsys, monkeypatch):
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, CLEAN_MEASURE_STDOUT)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = sim_run.main(["--workspace", str(ws), "--corners", "tt"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["status"] == "pass"


def test_netlist_path_is_absolute_for_a_relative_workspace(tmp_path, monkeypatch):
    # ngspice runs with cwd=log/sim, so a relative --workspace (the form
    # SKILL.md shows) once rendered an .include no deck could find
    monkeypatch.chdir(tmp_path)
    corner = {"process": "typical", "temp_c": 27, "supply_pct": 0}
    rel = sim_run.build_subs(tmp_path, Path("blocks/b/netlist/b.cir"), corner, 3.3, {})
    assert rel["NETLIST"] == str(tmp_path / "blocks/b/netlist/b.cir")
    absolute = tmp_path / "x.cir"
    assert sim_run.build_subs(tmp_path, absolute, corner, 3.3, {})["NETLIST"] == str(absolute)
