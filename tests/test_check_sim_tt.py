"""engine/scripts/check_sim_tt.py: sim_run.py at the single 'tt' corner
(docs/design.md 1.5, "### M8."). Fast tests fake `eda`; the `slow` tests
below run the REAL gf180mcuD toolchain against the real corpus/ade/mirror
rung - the gate-level proof this milestone's brief asks for ("prove with a
test that an ngspice run that errors, fails to converge, or produces a
measure it never printed fails the gate")."""
from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
CORPUS_MIRROR = REPO / "corpus" / "ade" / "mirror"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import check_sim_tt  # noqa: E402
import sim_run  # noqa: E402

SPEC_YAML = """\
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


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb"):
        (ws / sub).mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(SPEC_YAML, encoding="utf-8")
    (ws / "netlist" / "mirror.cir").write_text("* netlist\n", encoding="utf-8")
    (ws / "tb" / "mirror_tb.cir").write_text(BENCH_TEMPLATE, encoding="utf-8")
    (ws / "tb" / "mirror_tb.bounds.json").write_text(
        json.dumps([{"measure": "iout_ratio", "min": 1.8, "max": 2.2}]),
        encoding="utf-8")
    return ws


def make_fake_eda(tmp_path: Path, stdout: str) -> Path:
    fake_root = tmp_path / "fake_toolchain"
    (fake_root / "foss" / "pdks" / "gf180mcuD").mkdir(parents=True)
    script = tmp_path / "fake_eda"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --print-toolchain-root ]; then\n"
        f"  echo '{fake_root}'\n  exit 0\nfi\n"
        f"cat <<'STDOUT'\n{stdout}\nSTDOUT\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_sim_tt_only_runs_the_tt_corner(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, "\n  Measurements for Transient Analysis\n"
                                  "iout_ratio            =  2.00000e+00\n")
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_sim_tt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["corners"] == ["tt"]


def test_sim_tt_fails_when_measure_out_of_bound(tmp_path, monkeypatch, capsys):
    # "W and L swapped on a mirror's output device" (gates.yaml's named
    # fault) would drive iout_ratio wildly off its expected ~2.0 - modeled
    # here as the bound simply failing.
    ws = make_ws(tmp_path)
    eda = make_fake_eda(tmp_path, "\n  Measurements for Transient Analysis\n"
                                  "iout_ratio            =  9.00000e+00\n")
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_sim_tt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["violations"][0]["kind"] == "sim_bound_fail"


def make_real_mirror_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb"):
        (ws / sub).mkdir(parents=True)
    shutil.copy2(CORPUS_MIRROR / "spec.yaml", ws / "spec" / "spec.yaml")
    shutil.copy2(CORPUS_MIRROR / "netlist" / "mirror.cir",
                ws / "netlist" / "mirror.cir")
    for f in (CORPUS_MIRROR / "tb").iterdir():
        shutil.copy2(f, ws / "tb" / f.name)
    return ws


@pytest.mark.slow
def test_real_mirror_passes_sim_tt(tmp_path, capsys):
    ws = make_real_mirror_ws(tmp_path)
    code = check_sim_tt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    # a real ngspice measurement, not a canned string - the whole point of
    # a `slow` case.
    assert 18e-6 <= out["results"][0]["measures"]["iout_raw"] <= 32e-6


@pytest.mark.slow
def test_real_w_and_l_swapped_fails_sim_tt(tmp_path, capsys):
    ws = make_real_mirror_ws(tmp_path)
    text = (ws / "netlist" / "mirror.cir").read_text(encoding="utf-8")
    (ws / "netlist" / "mirror.cir").write_text(text.replace(
        "xmout iout      iref_node vss vss nfet_03v3 w=8e-6 l=5e-7",
        "xmout iout      iref_node vss vss nfet_03v3 w=5e-7 l=8e-6"),
        encoding="utf-8")
    code = check_sim_tt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["violations"][0]["kind"] == "sim_bound_fail"


@pytest.mark.slow
def test_real_floating_gate_node_fails_despite_exit_0_and_clean_prints(tmp_path, capsys):
    # THE case this milestone's brief names by hand: a floating MOSFET gate
    # drives this box's real ngspice through singular-matrix -> failed
    # gmin/source stepping -> a "successful" op anyway, exit 0, with every
    # requested print/measure still there (see engine/lib/simlib.py's own
    # module docstring for the same transcript, captured while building
    # this gate). netlist_lint's own static scan would also catch the
    # floating node by inspection; this proves the DYNAMIC half - the real
    # engine-error detection - catches it too, independent of that.
    ws = make_real_mirror_ws(tmp_path)
    netlist = (ws / "netlist" / "mirror.cir").read_text(encoding="utf-8")
    # detach xmout's gate from the shared reference node - a real floating
    # gate, not a synthetic string.
    broken = netlist.replace(
        "xmout iout      iref_node vss vss nfet_03v3 w=8e-6 l=5e-7",
        "xmout iout      floatgate vss vss nfet_03v3 w=8e-6 l=5e-7\n"
        "cfloat floatgate 0 1f")
    (ws / "netlist" / "mirror.cir").write_text(broken, encoding="utf-8")
    code = check_sim_tt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert any(k.startswith("sim_engine_error") for k in kinds), out
