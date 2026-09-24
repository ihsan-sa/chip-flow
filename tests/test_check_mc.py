"""engine/scripts/check_mc.py: the mc gate, run only when spec.yaml asks for
it (docs/design.md 1.5, "### M8."). Fast: fakes `eda`; a real MC run relies
on the gf180 PDK's own live agauss() mismatch, exercised in the `slow`
smoke instead (tests/smoke-ade.sh, if present) rather than faked here."""
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

import check_mc  # noqa: E402
import sim_run  # noqa: E402

SPEC_YAML_NO_MC = """\
top: mirror
supply: {vdd: 3.3}
devices: [xmref, xmout]
measures:
  - {name: iout_ratio, bounds: {min: 1.8, max: 2.2}}
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


def make_ws(tmp_path: Path, mc_yaml: str = "") -> Path:
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb"):
        (ws / sub).mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(SPEC_YAML_NO_MC + mc_yaml,
                                           encoding="utf-8")
    (ws / "netlist" / "mirror.cir").write_text("* netlist\n", encoding="utf-8")
    (ws / "tb" / "mirror_tb.cir").write_text(BENCH_TEMPLATE, encoding="utf-8")
    (ws / "tb" / "mirror_tb.bounds.json").write_text(
        json.dumps([{"measure": "iout_ratio", "min": 1.8, "max": 2.2}]),
        encoding="utf-8")
    return ws


def make_fake_eda(tmp_path: Path, ratios: list[str]) -> Path:
    """Cycles through `ratios` on successive invocations (a call counter
    file), so a run sequence can be scripted precisely."""
    fake_root = tmp_path / "fake_toolchain"
    (fake_root / "foss" / "pdks" / "gf180mcuD").mkdir(parents=True)
    counter = tmp_path / "counter"
    counter.write_text("0", encoding="utf-8")
    ratios_file = tmp_path / "ratios"
    ratios_file.write_text("\n".join(ratios), encoding="utf-8")
    script = tmp_path / "fake_eda"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --print-toolchain-root ]; then\n"
        f"  echo '{fake_root}'\n  exit 0\nfi\n"
        f"n=$(cat '{counter}')\n"
        f"ratio=$(sed -n \"$((n+1))p\" '{ratios_file}')\n"
        f"echo $((n+1)) > '{counter}'\n"
        "echo\n"
        "echo '  Measurements for Transient Analysis'\n"
        "echo \"iout_ratio            =  $ratio\"\n",
        encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_not_applicable_when_no_mc_block(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, ["2.0"])
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_mc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["applicable"] is False
    assert out["violations"] == []


def test_not_applicable_when_disabled(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path, "mc: {enabled: false}\n")
    eda = make_fake_eda(tmp_path, ["2.0"])
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_mc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["applicable"] is False


def test_yield_at_or_above_spec_passes(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path, "mc: {enabled: true, runs: 4, yield_min: 0.75}\n")
    eda = make_fake_eda(tmp_path, ["2.0", "2.0", "2.0", "9.0"])  # 3/4 = 0.75
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_mc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["applicable"] is True
    assert out["yield_frac"] == 0.75


def test_yield_below_spec_fails(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path, "mc: {enabled: true, runs: 4, yield_min: 0.9}\n")
    eda = make_fake_eda(tmp_path, ["2.0", "2.0", "2.0", "9.0"])  # 0.75 < 0.9
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_mc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "yield_below_spec" in kinds
