"""engine/scripts/check_mc.py: the mc gate, run only when spec.yaml asks for
it (docs/design.md 1.5, "### M8."). Fast: fakes `eda`; a real MC run relies
on the gf180 PDK's own live agauss() mismatch and its own `.option seed=`
handling - test_real_mirror_mc_yields_a_spread (slow) proves that against
the real toolchain, since a fake eda that ignores seed/mismatch entirely
could otherwise "pass" a gate that is secretly running one sample 20
times."""
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


def test_runs_zero_is_refused_not_defaulted(tmp_path, monkeypatch, capsys):
    # `0 or DEFAULT_RUNS` used to silently widen an explicit "run zero
    # samples" back up to 20 - Python's `or` treats 0 as falsy. This must
    # refuse outright instead.
    ws = make_ws(tmp_path, "mc: {enabled: true, runs: 0, yield_min: 0.5}\n")
    eda = make_fake_eda(tmp_path, ["2.0"] * 20)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_mc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "runs" in out.get("error", "")


def make_seed_dependent_fake_eda(tmp_path: Path) -> Path:
    """The ratio it reports is a function of the deck's own `.option
    seed=N` value - proving check_mc.py actually threads a distinct seed
    into each of `mc.runs` decks (never the same deck run twice), with no
    real ngspice/agauss() call."""
    fake_root = tmp_path / "fake_toolchain"
    (fake_root / "foss" / "pdks" / "gf180mcuD").mkdir(parents=True)
    script = tmp_path / "fake_eda"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --print-toolchain-root ]; then\n"
        f"  echo '{fake_root}'\n  exit 0\nfi\n"
        "deck=\"$3\"\n"
        "seed=$(grep -oE '\\.option seed=[0-9]+' \"$deck\" | head -1 | "
        "cut -d= -f2)\n"
        "seed=${seed:-0}\n"
        "ratio=$(awk -v s=\"$seed\" "
        "'BEGIN{printf \"%.6f\", 2.0 + (s % 10) * 0.001}')\n"
        "echo\n"
        "echo '  Measurements for Transient Analysis'\n"
        "echo \"iout_ratio            =  $ratio\"\n",
        encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_seeds_are_injected_recorded_and_vary_the_result(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path, "mc: {enabled: true, runs: 5, yield_min: 0.0, "
                          "seed: 100}\n")
    eda = make_seed_dependent_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_mc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["seeds"] == [100, 101, 102, 103, 104]
    seen_measures = {r["measures"]["iout_ratio"] for r in out["results"]}
    assert len(seen_measures) == 5, out  # every seed produced a distinct sample


def test_bit_identical_samples_across_runs_is_refused(tmp_path, monkeypatch, capsys):
    # A fake eda that ignores seed entirely (the bug this whole gate exists
    # to catch: sw_stat_mismatch/seed injection silently doing nothing, so
    # 20 "runs" are one sample wearing a yield percentage) must be refused,
    # never reported as a clean 100% yield.
    ws = make_ws(tmp_path, "mc: {enabled: true, runs: 5, yield_min: 0.5}\n")
    eda = make_fake_eda(tmp_path, ["2.0"] * 5)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_mc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "identical" in out.get("error", "").lower()


def test_every_sample_failed_is_always_a_violation(tmp_path, monkeypatch, capsys):
    # yield_min set low enough that the old `yield_frac < yield_min` check
    # alone would pass a run where EVERY sample failed - that must still be
    # refused. Ratios vary slightly per run so this is not also caught by
    # the bit-identical check above.
    ws = make_ws(tmp_path, "mc: {enabled: true, runs: 4, yield_min: 0.0}\n")
    eda = make_fake_eda(tmp_path, ["9.00", "9.01", "9.02", "9.03"])
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_mc.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["hits"] == 0
    kinds = {v["kind"] for v in out["violations"]}
    assert "yield_all_failed" in kinds


@pytest.mark.slow
def test_real_mirror_mc_yields_a_spread(tmp_path):
    # The real fault this test guards: a fake ngspice can be made to return
    # whatever a test script wants, so only a REAL run against the PDK's
    # own agauss()-driven mismatch proves the injected sw_stat_mismatch=1 +
    # per-run seed actually varies iout_raw from run to run.
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb"):
        (ws / sub).mkdir(parents=True)
    spec_text = (CORPUS_MIRROR / "spec.yaml").read_text(encoding="utf-8")
    spec_text += "mc: {enabled: true, runs: 8, yield_min: 0.0}\n"
    (ws / "spec" / "spec.yaml").write_text(spec_text, encoding="utf-8")
    shutil.copy2(CORPUS_MIRROR / "netlist" / "mirror.cir",
                ws / "netlist" / "mirror.cir")
    for f in (CORPUS_MIRROR / "tb").iterdir():
        shutil.copy2(f, ws / "tb" / f.name)

    payload, _ = check_mc.run(["--workspace", str(ws)])
    assert payload["applicable"] is True
    assert payload["runs"] == 8
    values = [r["measures"]["iout_raw"] for r in payload["results"]]
    assert len(set(values)) > 1, values  # never one sample repeated
    mean = sum(values) / len(values)
    stddev = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
    assert stddev > 0, values
