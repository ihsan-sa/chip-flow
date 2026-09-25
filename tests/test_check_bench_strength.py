"""engine/scripts/check_bench_strength.py: analog's device-mutant gate
(docs/design.md 1.5, section 2, "### M8."). Fast: fakes `eda` so ngspice
reports the mutated netlist's own W (grepped straight out of the deck it was
handed) as the measured ratio - a real, if simplified, model of "size
doubled pushes the ratio out of bounds", with no real ngspice call."""
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

import check_bench_strength  # noqa: E402
import sim_run  # noqa: E402

SPEC_YAML = """\
top: mirror
supply: {vdd: 3.3}
devices: [xmref, xmout, iref]
measures:
  - {name: iout_ratio, bounds: {min: 1.8, max: 2.2}}
"""

NETLIST = """\
.subckt current_mirror iref_node iout vdd vss
xmref iref_node iref_node vss vss nfet_03v3 w=4e-6 l=5e-7
xmout iout iref_node vss vss nfet_03v3 w=8e-6 l=5e-7
.ends current_mirror
"""

BENCH_TEMPLATE = """\
.include '{{PDK}}/libs.tech/ngspice/design.spice'
.lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' {{CORNER}}
.temp {{TEMP_C}}
.include '{{NETLIST}}'
{{SIZING}}
vdd vdd 0 {{VDD}}
iref vdd iref_node dc 10e-6
xmirror iref_node iout vdd 0 current_mirror
.control
op
print v(iout)
.endc
.end
"""


def make_ws(tmp_path: Path, netlist_text: str = NETLIST) -> Path:
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


def make_reference_check_fake_eda(tmp_path: Path) -> Path:
    """A generic mutant detector, not specific to one mutation kind: the
    deck it was handed (plus whatever file its own `.include` line points
    at - a netlist-targeted mutant's scratch copy, or the real netlist for
    a bench-targeted one) must still contain BOTH reference device lines
    verbatim (xmref/xmout, from the netlist) and the reference bias line
    verbatim (iref, from the bench) - size/type/connection/bias mutations
    each alter exactly one of those three lines, so "all three survive
    untouched" is a clean, mutation-kind-agnostic pass/fail signal with no
    real ngspice call."""
    fake_root = tmp_path / "fake_toolchain"
    (fake_root / "foss" / "pdks" / "gf180mcuD").mkdir(parents=True)
    script = tmp_path / "fake_eda"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --print-toolchain-root ]; then\n"
        f"  echo '{fake_root}'\n  exit 0\nfi\n"
        "deck=\"$3\"\n"
        "inc=$(grep -oE \"\\.include '[^']+'\" \"$deck\" | tail -1 | "
        "sed -E \"s/\\.include '//; s/'$//\")\n"
        "combined=\"$(cat \"$deck\") $(cat \"$inc\" 2>/dev/null)\"\n"
        "ok=1\n"
        "echo \"$combined\" | grep -qF "
        "'xmref iref_node iref_node vss vss nfet_03v3 w=4e-6 l=5e-7' || ok=0\n"
        "echo \"$combined\" | grep -qF "
        "'xmout iout iref_node vss vss nfet_03v3 w=8e-6 l=5e-7' || ok=0\n"
        "echo \"$combined\" | grep -qF 'iref vdd iref_node dc 10e-6' || ok=0\n"
        "if [ \"$ok\" = 1 ]; then ratio=2.0; else ratio=9.0; fi\n"
        "echo\n"
        "echo '  Measurements for Transient Analysis'\n"
        "echo \"iout_ratio            =  $ratio\"\n",
        encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_all_mutants_killed_passes(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    eda = make_reference_check_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_bench_strength.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["survived"] == 0
    assert out["total_mutants"] > 0


def test_bounds_wide_enough_to_pass_anything_is_the_named_fault(tmp_path, monkeypatch, capsys):
    # gates.yaml's own fault for bench_strength.
    ws = make_ws(tmp_path)
    (ws / "tb" / "mirror_tb.bounds.json").write_text(
        json.dumps([{"measure": "iout_ratio", "min": -1e9, "max": 1e9}]),
        encoding="utf-8")
    eda = make_reference_check_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_bench_strength.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["survived"] == out["total_mutants"]
    kinds = {v["kind"] for v in out["violations"]}
    assert any(k.startswith("survivor_") for k in kinds)


def test_bias_halved_mutant_is_generated_for_a_declared_source(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    eda = make_reference_check_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_bench_strength.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    kinds = {m["kind"] for m in out["mutants"].values()}
    assert "bias_halved" in kinds
    assert "size_doubled" in kinds
    assert "type_flipped" in kinds
    assert "connection_removed" in kinds


def test_mutant_decks_carry_the_blocks_own_sizing(tmp_path, monkeypatch, capsys):
    # A block sized by sizing/sizing.yaml leaves its netlist's params
    # undefined without it: a mutant deck built without the sizing makes
    # ngspice error out, and that error used to count as a kill.
    ws = make_ws(tmp_path)
    (ws / "sizing").mkdir()
    (ws / "sizing" / "sizing.yaml").write_text("w_probe: 4e-6\n",
                                               encoding="utf-8")
    eda = make_reference_check_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_bench_strength.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    decks = list((ws / "log" / "bench_strength").glob("*__tt.cir"))
    mutant_decks = [d for d in decks if "w_probe" in d.read_text()]
    assert decks and len(mutant_decks) == len(decks)


def test_a_baseline_that_does_not_pass_is_a_refusal_not_a_kill(tmp_path, monkeypatch, capsys):
    # Every mutant "killed" means nothing when the unmutated design fails
    # the same deck too: the gate refuses instead of passing.
    ws = make_ws(tmp_path)
    (ws / "tb" / "mirror_tb.bounds.json").write_text(
        json.dumps([{"measure": "iout_ratio", "min": 5.0, "max": 6.0}]),
        encoding="utf-8")
    eda = make_reference_check_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_bench_strength.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "baseline" in out["error"]
    assert out.get("remediation")
