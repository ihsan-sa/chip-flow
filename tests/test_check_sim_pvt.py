"""engine/scripts/check_sim_pvt.py: sim_run.py over the block's corner set
(docs/design.md 1.5, "### M8."). Fast: fakes `eda`, varying its reply by the
corner name found in the deck it was asked to run (so ss/hot behaves worse
than tt) - proving the "meets at typical, loses headroom at slow and hot"
fault shape end to end without a real ngspice call."""
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

import check_sim_pvt  # noqa: E402
import sim_run  # noqa: E402

SPEC_YAML = """\
top: mirror
supply: {vdd: 3.3}
devices: [xmref, xmout]
measures:
  - {name: iout_ratio, bounds: {min: 1.95, max: 2.05}}
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


def make_ws(tmp_path: Path, corners_field: str | None = None) -> Path:
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb"):
        (ws / sub).mkdir(parents=True)
    spec_text = SPEC_YAML
    if corners_field:
        spec_text += f"corners: {corners_field}\n"
    (ws / "spec" / "spec.yaml").write_text(spec_text, encoding="utf-8")
    (ws / "netlist" / "mirror.cir").write_text("* netlist\n", encoding="utf-8")
    (ws / "tb" / "mirror_tb.cir").write_text(BENCH_TEMPLATE, encoding="utf-8")
    (ws / "tb" / "mirror_tb.bounds.json").write_text(
        json.dumps([{"measure": "iout_ratio", "min": 1.95, "max": 2.05}]),
        encoding="utf-8")
    return ws


def make_corner_sensitive_fake_eda(tmp_path: Path) -> Path:
    """`ss` (the "slow and hot" default corner) reports a ratio outside
    bounds; every other corner reports a clean 2.0 - the fake ngspice tells
    corners apart by grepping the deck it was handed for the `.lib ...`
    corner name sim_run.py itself materializes into it."""
    fake_root = tmp_path / "fake_toolchain"
    (fake_root / "foss" / "pdks" / "gf180mcuD").mkdir(parents=True)
    script = tmp_path / "fake_eda"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --print-toolchain-root ]; then\n"
        f"  echo '{fake_root}'\n  exit 0\nfi\n"
        "deck=\"$3\"\n"
        "if grep -q \"sm141064.spice' ss\" \"$deck\"; then\n"
        "  ratio=9.00000e+00\n"
        "else\n"
        "  ratio=2.00000e+00\n"
        "fi\n"
        "echo\n"
        "echo '  Measurements for Transient Analysis'\n"
        "echo \"iout_ratio            =  $ratio\"\n",
        encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_default_corner_set_is_five(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    eda = make_corner_sensitive_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert set(out["corners"]) == {"tt", "ss", "ff", "sf", "fs"}
    assert code == 1, out  # ss fails


def test_meets_at_typical_loses_headroom_at_slow_and_hot(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    eda = make_corner_sensitive_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    bad_corners = {v["refs"][1] for v in out["violations"]
                  if v["kind"] == "sim_bound_fail"}
    assert bad_corners == {"ss"}
    tt_result = next(r for r in out["results"] if r["corner"] == "tt")
    assert tt_result["violations"] == []


def test_explicit_corner_list_cannot_skip_a_default(tmp_path, monkeypatch, capsys):
    # A spec naming only `[tt, ff]` used to run ONLY those two - ss (the
    # "slow and hot" default corner) never ran, so its bound violation never
    # showed up. design.md 5's five defaults are "never fewer": an explicit
    # list is unioned with them, not a replacement.
    ws = make_ws(tmp_path, corners_field="[tt, ff]")
    eda = make_corner_sensitive_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out  # ss still runs, and still fails
    assert set(out["corners"]) == {"tt", "ss", "ff", "sf", "fs"}
    bad_corners = {v["refs"][1] for v in out["violations"]
                  if v["kind"] == "sim_bound_fail"}
    assert bad_corners == {"ss"}


def test_explicit_corner_list_can_add_beyond_default(tmp_path, monkeypatch, capsys):
    # add-corner's own contract (skills/ade/reference/tasks.yaml): naming a
    # corner in the list is how a spec asks for MORE than the default five,
    # never fewer. Naming a default-set corner explicitly is a no-op (it
    # already runs) but must still not drop any of the other four.
    ws = make_ws(tmp_path, corners_field="[tt]")
    eda = make_corner_sensitive_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert set(out["corners"]) == {"tt", "ss", "ff", "sf", "fs"}


@pytest.mark.slow
def test_real_mirror_passes_every_default_corner(tmp_path, capsys):
    # the real fault this row names: "meets at typical, loses headroom at
    # slow and hot" - proving the CLEAN reference genuinely clears every
    # corner (not just typical) is what makes that fault meaningful.
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb"):
        (ws / sub).mkdir(parents=True)
    shutil.copy2(CORPUS_MIRROR / "spec.yaml", ws / "spec" / "spec.yaml")
    shutil.copy2(CORPUS_MIRROR / "netlist" / "mirror.cir",
                ws / "netlist" / "mirror.cir")
    for f in (CORPUS_MIRROR / "tb").iterdir():
        shutil.copy2(f, ws / "tb" / f.name)
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert set(out["corners"]) == {"tt", "ss", "ff", "sf", "fs"}
    for r in out["results"]:
        assert 18e-6 <= r["measures"]["iout_raw"] <= 32e-6, r
