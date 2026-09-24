"""engine/scripts/optimise.py: the sizing optimise loop, `start` + `numeric`
(docs/design.md section 4, "### M8."). Fast tests fake `eda` with a simple,
deterministic function of the sizing parameter it was handed (y = x), so
scipy's real differential_evolution runs against a trivial but REAL
objective with no actual ngspice call. test_real_r2r_dac_optimise_reaches_
bounds_from_wrong_start (slow) runs the whole thing against the REAL
corpus/ade/r2r_dac rung and the real gf180mcuD toolchain - this milestone's
own done criterion."""
from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
CORPUS_R2R = REPO / "corpus" / "ade" / "r2r_dac"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import optimise  # noqa: E402
import sim_run  # noqa: E402
from checklib import CheckError  # noqa: E402

import pytest  # noqa: E402

SPEC_YAML = "top: widget\nsupply: {vdd: 3.3}\ndevices: [xm1]\nrequirements: []\n"

BENCH_TEMPLATE = """\
.include '{{PDK}}/libs.tech/ngspice/design.spice'
.lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' {{CORNER}}
.temp {{TEMP_C}}
.include '{{NETLIST}}'
{{SIZING}}
.control
op
print v(vdd)
.endc
.end
"""


def make_ws(tmp_path: Path, start_value: float = -3.0) -> Path:
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb", "sizing"):
        (ws / sub).mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(SPEC_YAML, encoding="utf-8")
    (ws / "netlist" / "widget.cir").write_text("* netlist\n", encoding="utf-8")
    (ws / "tb" / "widget_tb.cir").write_text(BENCH_TEMPLATE, encoding="utf-8")
    (ws / "tb" / "widget_tb.bounds.json").write_text(
        json.dumps([{"measure": "y", "min": 0.9, "max": 1.1}]),
        encoding="utf-8")
    import yaml
    (ws / "sizing" / "sizing.yaml").write_text(yaml.safe_dump({
        "x": {"value": start_value, "min": -5.0, "max": 5.0}}), encoding="utf-8")
    return ws


def make_identity_fake_eda(tmp_path: Path) -> Path:
    """Reports measure y = the deck's own `.param x=...` value - a trivial,
    real (not mocked out) function scipy's differential_evolution optimizes
    against, with no ngspice call."""
    fake_root = tmp_path / "fake_toolchain"
    (fake_root / "foss" / "pdks" / "gf180mcuD").mkdir(parents=True)
    script = tmp_path / "fake_eda"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --print-toolchain-root ]; then\n"
        f"  echo '{fake_root}'\n  exit 0\nfi\n"
        "deck=\"$3\"\n"
        "x=$(grep -oE '\\.param x=[-0-9.eE+]+' \"$deck\" | cut -d= -f2)\n"
        "echo\n"
        "echo '  Measurements for Transient Analysis'\n"
        "echo \"y                     =  $x\"\n",
        encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_start_freezes_evaluator_and_writes_empty_tsv(tmp_path):
    ws = make_ws(tmp_path)
    payload, _ = optimise.run_start(
        ["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    assert payload["status"] == "pass"
    meta = json.loads((ws / optimise.META_PATH).read_text())
    assert meta["target"] == "sizing/sizing.yaml"
    tsv_lines = (ws / optimise.TSV_PATH).read_text().splitlines()
    assert tsv_lines == ["\t".join(optimise.TSV_FIELDS)]


def test_numeric_without_start_raises(tmp_path, monkeypatch):
    ws = make_ws(tmp_path)
    eda = make_identity_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    with pytest.raises(CheckError):
        optimise.run_numeric(["--workspace", str(ws)])


def test_numeric_detects_evaluator_drift(tmp_path, monkeypatch):
    ws = make_ws(tmp_path)
    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    # a bench edit after `start` must be caught, not silently trusted.
    (ws / "tb" / "widget_tb.cir").write_text(BENCH_TEMPLATE + "* edited\n",
                                             encoding="utf-8")
    eda = make_identity_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    with pytest.raises(CheckError):
        optimise.run_numeric(["--workspace", str(ws)])


def test_numeric_reaches_bounds_from_a_wrong_start(tmp_path, monkeypatch):
    ws = make_ws(tmp_path, start_value=-3.0)  # deliberately wrong: -3 is
    # nowhere near [0.9, 1.1], the bound y=x must land inside.
    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    eda = make_identity_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    payload, _ = optimise.run_numeric(
        ["--workspace", str(ws), "--popsize", "8", "--maxiter", "20", "--seed", "1"])
    assert payload["status"] == "pass", payload
    assert payload["improved"] is True
    assert payload["best_score"] >= 0
    import yaml
    final = yaml.safe_load((ws / "sizing" / "sizing.yaml").read_text())
    assert 0.9 <= final["x"]["value"] <= 1.1

    tsv_lines = (ws / optimise.TSV_PATH).read_text().splitlines()
    assert len(tsv_lines) > 2  # header + start row + at least one DE trial
    assert tsv_lines[1].split("\t")[-1] == "starting sizing"


def test_numeric_leaves_file_untouched_when_nothing_improves(tmp_path, monkeypatch):
    # start already at the optimum (y = x = 1.0, comfortably inside
    # [0.9, 1.1]) - the search must not report an "improvement" that never
    # happened, and must never rewrite a file it did not actually beat.
    ws = make_ws(tmp_path, start_value=1.0)
    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    eda = make_identity_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    payload, _ = optimise.run_numeric(
        ["--workspace", str(ws), "--popsize", "6", "--maxiter", "10", "--seed", "1"])
    assert payload["status"] == "pass", payload
    import yaml
    final = yaml.safe_load((ws / "sizing" / "sizing.yaml").read_text())
    assert final["x"]["value"] == 1.0


@pytest.mark.slow
def test_real_r2r_dac_optimise_reaches_bounds_from_wrong_start(tmp_path):
    """This milestone's own done criterion, run for real: corpus/ade/r2r_dac
    ships with a deliberately wrong 1:1 R:2R ratio (netlist/r2r_dac.cir's
    own comment explains why sim_tt is not in that rung's faults/
    manifest.yaml) - `optimise.py numeric` against its real sizing.yaml and
    the real gf180mcuD toolchain must reach every measure inside bounds."""
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb", "sizing"):
        (ws / sub).mkdir(parents=True)
    shutil.copy2(CORPUS_R2R / "spec.yaml", ws / "spec" / "spec.yaml")
    shutil.copy2(CORPUS_R2R / "netlist" / "r2r_dac.cir",
                ws / "netlist" / "r2r_dac.cir")
    for f in (CORPUS_R2R / "tb").iterdir():
        shutil.copy2(f, ws / "tb" / f.name)
    shutil.copy2(CORPUS_R2R / "sizing" / "sizing.yaml",
                ws / "sizing" / "sizing.yaml")

    import yaml
    start = yaml.safe_load((ws / "sizing" / "sizing.yaml").read_text())
    assert start["r_unit"]["value"] == start["r2_unit"]["value"], (
        "the shipped corpus rung is supposed to start wrong (1:1) - if this "
        "ever fires, the corpus file itself changed under this test")

    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    payload, _ = optimise.run_numeric(
        ["--workspace", str(ws), "--popsize", "8", "--maxiter", "20", "--seed", "1"])

    assert payload["status"] == "pass", payload
    assert payload["improved"] is True
    assert payload["best_score"] >= 0

    final = yaml.safe_load((ws / "sizing" / "sizing.yaml").read_text())
    ratio = final["r2_unit"]["value"] / final["r_unit"]["value"]
    assert ratio == pytest.approx(2.0, rel=0.05)

    # and the sim_tt gate itself, which failed on the untouched rung, now
    # passes against the optimiser's own winning sizing.
    sys.path.insert(0, str(SCRIPTS))
    import check_sim_tt
    code = check_sim_tt.main(["--workspace", str(ws)])
    assert code == 0
