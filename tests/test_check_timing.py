"""engine/scripts/check_timing.py: the timing gate (docs/design.md 1.5's
timing row, "### M4."). Fakes `eda sta`'s own output (never the real
OpenSTA - tests/smoke-harden.sh runs that) to prove the gate's failure
classification: a launcher that never produces a parseable "worst slack"
line is a refusal, a negative slack is a finding, never a silent pass.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_timing  # noqa: E402
import pytest  # noqa: E402
import ttlib  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "opensta"
CLEAN_COUNTS = "chipflow_violation_counts slew 0 cap 0 fanout 0\n"


def make_ws_with_harden(tmp_path: Path, corners=("nom_tt_025C_3v30",)) -> Path:
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\n"
        "ports: {clk: {dir: input, width: 1}}\n"
        "tt_pins: {clk: clk}\n", encoding="utf-8")
    final = ws / "harden" / "runs" / "run" / "final"
    top = "tt_um_counter8"
    for corner in corners:
        (final / "lib" / corner).mkdir(parents=True)
        (final / "nl").mkdir(parents=True, exist_ok=True)
        (final / "nl" / f"{top}.nl.v").write_text("x", encoding="utf-8")
        (final / "sdc").mkdir(parents=True, exist_ok=True)
        (final / "sdc" / f"{top}.sdc").write_text("x", encoding="utf-8")
        bucket = corner.split("_", 1)[0]
        (final / "spef" / bucket).mkdir(parents=True, exist_ok=True)
        (final / "spef" / bucket / f"{top}.{bucket}.spef").write_text("x", encoding="utf-8")
    return ws


def _patch_common(monkeypatch, tmp_path):
    # a fake, always-existing liberty file - the real one lives in the PDK,
    # outside anything this test controls or needs.
    fake_lib = tmp_path / "fake.lib"
    fake_lib.write_text("x", encoding="utf-8")
    monkeypatch.setattr(ttlib, "stdcell_liberty_path", lambda corner, pdk_root: fake_lib)

    def fake_pdk_root(*a, **kw):
        return Path("/fake/toolchain/foss/pdks")
    monkeypatch.setattr(check_timing, "_pdk_root", fake_pdk_root)


def test_no_harden_output_refuses(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(
        "top: counter8\nrequirements: []\nports: {}\ntt_pins: {}\n",
        encoding="utf-8")
    code = check_timing.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "has not produced" in out["error"]


def test_sta_with_no_parseable_slack_refuses(tmp_path, monkeypatch, capsys):
    ws = make_ws_with_harden(tmp_path)
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="Error: something broke\n", stderr="")
    monkeypatch.setattr(check_timing.subprocess, "run", fake_run)

    code = check_timing.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no parseable worst-slack" in out["error"]


def test_negative_setup_slack_is_a_violation(tmp_path, monkeypatch, capsys):
    ws = make_ws_with_harden(tmp_path, corners=("max_ss_125C_3v00",))
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="worst slack max -0.06\nworst slack min 2.23\n" + CLEAN_COUNTS, stderr="")
    monkeypatch.setattr(check_timing.subprocess, "run", fake_run)

    code = check_timing.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["status"] == "violations"
    kinds = {v["kind"] for v in out["violations"]}
    assert "setup_violation" in kinds


def test_all_corners_positive_slack_pass(tmp_path, monkeypatch, capsys):
    ws = make_ws_with_harden(tmp_path, corners=("nom_tt_025C_3v30", "max_ss_125C_3v00"))
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="worst slack max 7.97\nworst slack min 2.23\n" + CLEAN_COUNTS, stderr="")
    monkeypatch.setattr(check_timing.subprocess, "run", fake_run)

    code = check_timing.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert len(out["corners"]) == 2

    # Regression: the per-corner .sta_<corner>.tcl scratch file used to be
    # written straight into harden/runs/run/final/, the exact tree the
    # "harden" artifact-kind's dir_text hash recursively covers
    # (invalidation.yaml) - a write there staled every OTHER gate that also
    # reads "harden". It must land under ws/log/timing_work/ instead, and
    # final/ must stay exactly what harden itself produced (nothing new).
    final_dir = ws / "harden" / "runs" / "run" / "final"
    assert not list(final_dir.glob(".sta_*")), \
        "check_timing wrote a scratch file into final_dir"
    work_dir = ws / "log" / "timing_work"
    assert len(list(work_dir.glob(".sta_*.tcl"))) == 2, \
        "expected one .sta_<corner>.tcl per corner under ws/log/timing_work/"


def _check_types_with_good_slack() -> str:
    """The real OpenSTA 3.1.0 report_check_types -violators output (a tiny
    design with a forced slew, capacitance and fanout violation), with its
    slack lines swapped for positive ones so only the check-types section
    can fail the gate."""
    lines = (FIXTURES / "check_types_violators.txt").read_text(
        encoding="utf-8").splitlines()
    body = [ln for ln in lines if not ln.startswith("worst slack")]
    return "worst slack max 7.97\nworst slack min 2.23\n" + "\n".join(body) + "\n"


def test_parse_check_types_reads_the_real_opensta_format():
    parsed = check_timing.parse_check_types(_check_types_with_good_slack())
    assert parsed["slew"]["count"] == 25 and len(parsed["slew"]["pins"]) == 25
    assert parsed["cap"]["count"] == 8 and len(parsed["cap"]["pins"]) == 8
    assert parsed["fanout"] == {"count": 1, "pins": ["u0/ZN"]}
    assert "b0/ZN" in parsed["cap"]["pins"]


def test_parse_check_types_without_counts_line_refuses():
    text = (FIXTURES / "check_types_violators.txt").read_text(encoding="utf-8")
    text = "\n".join(ln for ln in text.splitlines()
                     if not ln.startswith(check_timing.COUNTS_MARK))
    with pytest.raises(check_timing.CheckError, match="never ran"):
        check_timing.parse_check_types(text)


def test_cap_and_fanout_violators_fail_with_positive_slack(tmp_path, monkeypatch, capsys):
    ws = make_ws_with_harden(tmp_path)
    _patch_common(monkeypatch, tmp_path)
    out_text = _check_types_with_good_slack()

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=out_text, stderr="")
    monkeypatch.setattr(check_timing.subprocess, "run", fake_run)

    code = check_timing.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    msgs = [v["msg"] for v in out["violations"]]
    assert any("max cap: 8" in m for m in msgs), msgs
    assert any("max fanout: 1" in m and "u0/ZN" in m for m in msgs), msgs
    assert any("max slew: 25" in m for m in msgs), msgs


def test_counts_line_missing_from_real_run_refuses(tmp_path, monkeypatch, capsys):
    ws = make_ws_with_harden(tmp_path)
    _patch_common(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="worst slack max 7.97\nworst slack min 2.23\n", stderr="")
    monkeypatch.setattr(check_timing.subprocess, "run", fake_run)

    code = check_timing.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert check_timing.COUNTS_MARK in out["error"]


TINY_NETLIST = """module tt_um_tiny(input clk, input a, output [7:0] y);
 wire n;
 gf180mcu_fd_sc_mcu7t5v0__inv_1 u0(.I(a), .ZN(n));
""" + "".join(f" gf180mcu_fd_sc_mcu7t5v0__inv_1 b{i}(.I(n), .ZN(y[{i}]));\n"
              for i in range(8)) + "endmodule\n"

TINY_SPEF = """*SPEF "IEEE 1481-1998"
*DESIGN "tt_um_tiny"
*DATE "x"
*VENDOR "x"
*PROGRAM "x"
*VERSION "x"
*DESIGN_FLOW "x"
*DIVIDER /
*DELIMITER :
*BUS_DELIMITER [ ]
*T_UNIT 1 NS
*C_UNIT 1 PF
*R_UNIT 1 OHM
*L_UNIT 1 HENRY

*D_NET n 0.001
*CONN
*I u0:ZN O
*I b0:I I
*END
"""

TINY_SDC = """create_clock -name clk -period 100 [get_ports clk]
set_input_delay 0 -clock clk [get_ports a]
set_output_delay 0 -clock clk [get_ports y]
"""


@pytest.mark.slow
@pytest.mark.parametrize("extra_sdc,expect", [
    ("", []),
    ("set_max_fanout 4 [current_design]\n", ["max fanout: 1"]),
    ("set_load 0.5 [get_ports y]\n", ["max cap: 8"]),
])
def test_real_opensta_catches_fanout_and_cap(tmp_path, extra_sdc, expect):
    """Real OpenSTA on an eight-way fanout: clean as written, and red on a
    fanout limit or an output load it breaks - with timing slack still
    positive, so only the check-types section can catch it."""
    corner = "nom_tt_025C_3v30"
    final = tmp_path / "final"
    for sub in ("nl", "sdc", "spef/nom"):
        (final / sub).mkdir(parents=True)
    (final / "nl" / "tt_um_tiny.nl.v").write_text(TINY_NETLIST, encoding="utf-8")
    (final / "sdc" / "tt_um_tiny.sdc").write_text(TINY_SDC + extra_sdc, encoding="utf-8")
    (final / "spef" / "nom" / "tt_um_tiny.nom.spef").write_text(TINY_SPEF, encoding="utf-8")
    r = check_timing.run_corner(final, "tt_um_tiny", corner,
                                check_timing._pdk_root(), tmp_path)
    assert r["setup_ws"] > 0 and r["hold_ws"] > 0, r
    for want in expect:
        assert any(v.startswith(want) for v in r["violators"]), r
    if not expect:
        assert r["violators"] == [], r
