"""engine/scripts/check_netlist_lint.py: the netlist_lint gate (docs/
design.md 1.5, "### M8."). Fast tests fake `eda` for the dynamic dry-run
half; test_check_netlist_lint_real.py (if present) or the `slow` cases here
prove the real gf180mcuD models."""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import check_netlist_lint  # noqa: E402
import sim_run  # noqa: E402

SPEC_YAML = """\
top: mirror
supply: {vdd: 3.3}
devices: [xmref, xmout]
measures:
  - {name: iout_ratio, bounds: {min: 1.8, max: 2.2}}
"""

GOOD_NETLIST = """\
* netlist/*.cir is a subckt library (never directly runnable on its own -
* see check_netlist_lint.py's own docstring) - iref/iout/vdd/vss are this
* block's own external pins, exempt from the floating-node scan.
.subckt current_mirror iref iout vdd vss
xmref iref iref vss vss nfet_03v3 w=4e-6 l=5e-7
xmout iout iref vss vss nfet_03v3 w=8e-6 l=5e-7
.ends current_mirror
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
print v(d1) v(d2)
.endc
.end
"""

CLEAN_STDOUT = """\

  Measurements for Transient Analysis

iout_ratio           =  2.00000e+00
"""


def make_ws(tmp_path: Path, netlist_text: str = GOOD_NETLIST,
           devices: str | None = None) -> Path:
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb"):
        (ws / sub).mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(SPEC_YAML, encoding="utf-8")
    (ws / "netlist" / "mirror.cir").write_text(netlist_text, encoding="utf-8")
    (ws / "tb" / "mirror_tb.cir").write_text(BENCH_TEMPLATE, encoding="utf-8")
    (ws / "tb" / "mirror_tb.bounds.json").write_text(
        json.dumps([{"measure": "iout_ratio", "min": 1.8, "max": 2.2}]),
        encoding="utf-8")
    return ws


def make_fake_eda(tmp_path: Path, stdout: str, stderr: str = "",
                  exit_code: int = 0) -> Path:
    fake_root = tmp_path / "fake_toolchain"
    (fake_root / "foss" / "pdks" / "gf180mcuD" / "libs.tech" / "ngspice").mkdir(
        parents=True)
    # a minimal real PDK ngspice model file so netlistlib.known_models()
    # sees the models this test's netlists actually use.
    models = fake_root / "foss" / "pdks" / "gf180mcuD" / "libs.tech" / "ngspice" / "sm141064.spice"
    models.write_text(".subckt nfet_03v3 d g s b\n.ends nfet_03v3\n"
                      ".subckt pfet_03v3 d g s b\n.ends pfet_03v3\n",
                      encoding="utf-8")
    (fake_root / "foss" / "pdks" / "gf180mcuD" / "libs.tech" / "ngspice"
    / "sm141064_mim.spice").write_text("", encoding="utf-8")
    script = tmp_path / "fake_eda"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --print-toolchain-root ]; then\n"
        f"  echo '{fake_root}'\n  exit 0\nfi\n"
        f"cat >&2 <<'STDERR'\n{stderr}\nSTDERR\n"
        f"cat <<'STDOUT'\n{stdout}\nSTDOUT\n"
        f"exit {exit_code}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_clean_netlist_passes(tmp_path, monkeypatch):
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, CLEAN_STDOUT)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_netlist_lint.main(["--workspace", str(ws)])
    assert code == 0, ws


def test_model_not_in_pdk_is_caught(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path, netlist_text=GOOD_NETLIST.replace(
        "nfet_03v3", "nfet_99v9"))
    eda = make_fake_eda(tmp_path, CLEAN_STDOUT)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_netlist_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "model_not_in_pdk" in kinds


def test_declared_device_missing_from_netlist(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path, netlist_text="xmref d1 d1 0 0 nfet_03v3 w=4e-6 l=5e-7\n")
    eda = make_fake_eda(tmp_path, CLEAN_STDOUT)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_netlist_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "declared_device_missing" in kinds


def test_floating_node_static_scan(tmp_path, monkeypatch, capsys):
    netlist = GOOD_NETLIST + "xmextra iout unused_gate 0 0 nfet_03v3 w=1e-6 l=1e-6\n"
    ws = make_ws(tmp_path, netlist_text=netlist)
    eda = make_fake_eda(tmp_path, CLEAN_STDOUT)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_netlist_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "floating_node" in kinds


def test_dry_run_engine_error_caught_even_with_clean_measures(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, CLEAN_STDOUT,
                        stderr="Warning: singular matrix:  check node d1\n")
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_netlist_lint.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "sim_engine_error_singular_matrix" in kinds
