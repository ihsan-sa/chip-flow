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


def test_a_baseline_warning_neither_refuses_nor_kills(tmp_path, monkeypatch, capsys):
    # sim_tt passes with a warning-severity miss, so bench_strength must
    # too; and that same warning in a mutant is not a kill.
    ws = make_ws(tmp_path)
    (ws / "tb" / "mirror_tb.bounds.json").write_text(json.dumps([
        {"measure": "iout_ratio", "min": 1.8, "max": 2.2},
        {"measure": "iout_ratio", "min": 5.0, "max": 6.0,
         "severity": "warning"}]), encoding="utf-8")
    eda = make_reference_check_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_bench_strength.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out


def test_a_mutant_that_only_repeats_the_baseline_warning_survives(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    (ws / "tb" / "mirror_tb.bounds.json").write_text(json.dumps([
        {"measure": "iout_ratio", "min": -1e9, "max": 1e9},
        {"measure": "iout_ratio", "min": 5.0, "max": 6.0,
         "severity": "warning"}]), encoding="utf-8")
    eda = make_reference_check_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_bench_strength.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out


COMPARATOR_SPEC = """\
top: strongarm_comparator
supply: {vdd: 3.3}
devices: [xmtail, xmin1]
measures:
  - {name: iout_ratio, bounds: {min: 1.8, max: 2.2}}
"""


def make_comparator_ws(tmp_path: Path, tail_line: str) -> Path:
    # bounds wide enough that every mutant survives, so each survivor's
    # message shows which terminal its connection_removed mutant floated.
    ws = make_ws(tmp_path, NETLIST.replace(
        "xmout iout iref_node vss vss nfet_03v3 w=8e-6 l=5e-7",
        "xmout iout iref_node vss vss nfet_03v3 w=8e-6 l=5e-7\n" + tail_line
        + "\nxmin1 iout iref_node tail vss nfet_03v3 w=2e-6 l=5e-7"))
    (ws / "spec" / "spec.yaml").write_text(COMPARATOR_SPEC, encoding="utf-8")
    (ws / "tb" / "mirror_tb.bounds.json").write_text(
        json.dumps([{"measure": "iout_ratio", "min": -1e9, "max": 1e9}]),
        encoding="utf-8")
    return ws


def test_every_survivor_fails_including_a_bulk_tied_to_source_device(tmp_path, monkeypatch, capsys):
    # No equivalent-mutant exception: xmtail's bulk is tied to its source,
    # so its connection_removed mutant floats the drain, and a bench that
    # misses it fails the gate like any other survivor.
    ws = make_comparator_ws(
        tmp_path, "xmtail tail iref_node vss vss nfet_03v3 w=4e-6 l=5e-7")
    monkeypatch.setattr(sim_run, "EDA_BIN", make_reference_check_fake_eda(tmp_path))
    code = check_bench_strength.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert "accepted_equivalents" not in out
    assert out["survived"] == out["total_mutants"]
    assert out["mutants"]["xmtail_connection_removed"]["describe"] == (
        "xmtail: drain (bulk tied to its own source) disconnected")
    # xmin1's bulk (vss) is not its source (tail): its bulk is floated
    assert out["mutants"]["xmin1_connection_removed"]["describe"] == (
        "xmin1: bulk disconnected")
    messages = " ".join(v["msg"] for v in out["violations"])
    assert "xmtail_connection_removed (xmtail: drain" in messages


# --- spec/mutant_rulings.yaml: the owner's per-mutant rulings -------------
# These stub run_mutant itself, so each case sets the exact measures a
# survivor reports: baseline iout_ratio 2.0, gain 10.0; every mutant not
# named in `survivors` is killed.

XMREF_LINE = "xmref iref_node iref_node vss vss nfet_03v3 w=4e-6 l=5e-7"
BASE = {"iout_ratio": 2.0, "gain": 10.0}


def make_ruled_ws(tmp_path: Path, monkeypatch, survivors: dict,
                  rulings: str | None) -> Path:
    """survivors: {mutant id: its measures}; rulings: the yaml text, or None
    for no file."""
    ws = make_ws(tmp_path)
    (ws / "tb" / "mirror_tb.bounds.json").write_text(json.dumps([
        {"measure": "iout_ratio", "min": 1.8, "max": 2.2},
        {"measure": "gain", "min": 9.0, "max": 11.0}]), encoding="utf-8")
    if rulings is not None:
        (ws / "spec" / "mutant_rulings.yaml").write_text(rulings,
                                                         encoding="utf-8")
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        make_reference_check_fake_eda(tmp_path))

    def fake_run_mutant(eda_bin, ws_, mutant, *args, **kw):
        mid = mutant["id"]
        if mid == "baseline":
            return {"violations": [], "measures": dict(BASE), "deck": "d"}
        if mid in survivors:
            return {"violations": [], "measures": dict(survivors[mid])}
        return {"violations": [{"msg": "killed"}], "measures": {}}

    monkeypatch.setattr(check_bench_strength, "run_mutant", fake_run_mutant)
    return ws


def run_gate(ws, capsys):
    code = check_bench_strength.main(["--workspace", str(ws)])
    return code, json.loads(capsys.readouterr().out)


def errors(out) -> list[dict]:
    # gate.py fails a gate on its fail severities (error), not on an
    # info finding - which still makes the script itself exit 1
    return [v for v in out["violations"] if v["severity"] == "error"]


EQUIV_RULING = """\
equivalent:
  - id: xmref_type_flipped
    ruling: "owner, 2026-09-25"
    evidence: "no bench can observe it"
"""


def test_an_equivalent_ruling_passes_only_the_listed_survivor(tmp_path, monkeypatch, capsys):
    survivors = {"xmref_type_flipped": BASE, "xmout_type_flipped": BASE}
    ws = make_ruled_ws(tmp_path, monkeypatch, survivors, EQUIV_RULING)
    code, out = run_gate(ws, capsys)
    assert code == 1 and len(errors(out)) == 1, out
    by_kind = {v["kind"]: v for v in out["violations"]}
    assert by_kind["equivalent_type_flipped"]["severity"] == "info"
    assert "xmref_type_flipped" in by_kind["equivalent_type_flipped"]["msg"]
    # the unlisted survivor still fails
    assert by_kind["survivor_type_flipped"]["severity"] == "error"
    assert "xmout_type_flipped" in by_kind["survivor_type_flipped"]["msg"]
    assert out["equivalent"] == 1 and out["below_spread"] == 0
    assert out["equivalent_ids"] == ["xmref_type_flipped"]
    assert out["survived"] == 2


def test_an_equivalent_ruling_alone_passes_the_gate(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_type_flipped": BASE}, EQUIV_RULING)
    code, out = run_gate(ws, capsys)
    assert code == 1 and not errors(out), out
    assert out["mutants"]["xmref_type_flipped"]["deltas"] == {
        "gain": 0.0, "iout_ratio": 0.0}
    assert "deltas" not in out["mutants"]["xmout_type_flipped"]


def test_an_equivalent_ruling_for_a_killed_mutant_is_refused(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch, {}, EQUIV_RULING)
    code, out = run_gate(ws, capsys)
    assert code == 2, out
    assert "kills" in out["error"] and out.get("remediation")


def test_a_ruling_for_an_unknown_mutant_is_refused(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch, {},
                       EQUIV_RULING.replace("xmref_type_flipped", "xmzz_gone"))
    code, out = run_gate(ws, capsys)
    assert code == 2, out
    assert "did not generate" in out["error"]


# xmref_connection_removed moves iout_ratio by +0.5% and gain by +0.1%
BELOW_MEASURES = {"iout_ratio": 2.01, "gain": 10.01}


def below_ruling(**over) -> str:
    fields = {"id": "xmref_connection_removed",
              "netlist_line": XMREF_LINE, "measure": "iout_ratio",
              "delta": 0.005, "sigma": 0.01,
              "ruling": "owner, 2026-09-25"}
    fields.update(over)
    lines = ["below_spread:"]
    first = True
    for k, v in fields.items():
        if v is None:
            continue
        lines.append(f"  {'- ' if first else '  '}{k}: {json.dumps(v)}")
        first = False
    return "\n".join(lines) + "\n"


def test_a_valid_below_spread_ruling_passes_only_the_listed_survivor(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_connection_removed": BELOW_MEASURES},
                       below_ruling())
    code, out = run_gate(ws, capsys)
    assert code == 1 and not errors(out), out
    (v,) = out["violations"]
    assert v["kind"] == "below_spread_connection_removed"
    assert v["severity"] == "info" and "+0.005" in v["msg"]
    assert out["below_spread_ids"] == ["xmref_connection_removed"]
    assert out["mutants"]["xmref_connection_removed"]["deltas"] == {
        "gain": 0.001, "iout_ratio": 0.005}


def test_a_below_spread_ruling_does_not_cover_another_survivor(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_connection_removed": BELOW_MEASURES,
                        "xmout_connection_removed": BELOW_MEASURES},
                       below_ruling())
    code, out = run_gate(ws, capsys)
    assert code == 1, out
    sev = {v["kind"]: v["severity"] for v in out["violations"]}
    assert sev == {"below_spread_connection_removed": "info",
                   "survivor_connection_removed": "error"}


def _refused(tmp_path, monkeypatch, capsys, rulings, measures=BELOW_MEASURES):
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_connection_removed": measures}, rulings)
    code, out = run_gate(ws, capsys)
    assert code == 2, out
    assert out.get("remediation")
    return out["error"]


def test_a_below_spread_ruling_missing_netlist_line_is_refused(tmp_path, monkeypatch, capsys):
    assert "netlist_line" in _refused(tmp_path, monkeypatch, capsys,
                                      below_ruling(netlist_line=None))


def test_a_below_spread_ruling_missing_delta_is_refused(tmp_path, monkeypatch, capsys):
    assert "'delta'" in _refused(tmp_path, monkeypatch, capsys,
                                 below_ruling(delta=None))


def test_a_below_spread_ruling_missing_sigma_is_refused(tmp_path, monkeypatch, capsys):
    assert "'sigma'" in _refused(tmp_path, monkeypatch, capsys,
                                 below_ruling(sigma=None))


def test_a_below_spread_ruling_missing_ruling_is_refused(tmp_path, monkeypatch, capsys):
    assert "'ruling'" in _refused(tmp_path, monkeypatch, capsys,
                                  below_ruling(ruling=None))


def test_a_netlist_line_not_in_the_netlist_is_refused(tmp_path, monkeypatch, capsys):
    err = _refused(tmp_path, monkeypatch, capsys,
                   below_ruling(netlist_line=XMREF_LINE.replace("4e-6", "5e-6")))
    assert "not a line" in err


def test_a_netlist_line_of_another_device_is_refused(tmp_path, monkeypatch, capsys):
    err = _refused(tmp_path, monkeypatch, capsys, below_ruling(
        netlist_line="xmout iout iref_node vss vss nfet_03v3 w=8e-6 l=5e-7"))
    assert "not the mutated device" in err


def test_a_measure_the_benches_do_not_bound_is_refused(tmp_path, monkeypatch, capsys):
    err = _refused(tmp_path, monkeypatch, capsys, below_ruling(measure="f"))
    assert "not a tt measure" in err


def test_a_delta_that_disagrees_with_the_measurement_is_refused(tmp_path, monkeypatch, capsys):
    # measured +0.005; tolerance 0.001 + 5% of it = 0.00125
    err = _refused(tmp_path, monkeypatch, capsys, below_ruling(delta=0.0065))
    assert "does not match" in err


def test_a_delta_above_three_sigma_and_two_percent_is_refused(tmp_path, monkeypatch, capsys):
    # +3% on iout_ratio: sigma 0.005 gives max(0.015, 0.02) = 0.02 < 0.03
    err = _refused(tmp_path, monkeypatch, capsys,
                   below_ruling(delta=0.03, sigma=0.005),
                   measures={"iout_ratio": 2.06, "gain": 10.0})
    assert "outside max(3 sigma, 2%)" in err


def test_naming_the_smaller_measure_passes_while_the_other_is_in_spread(tmp_path, monkeypatch, capsys):
    # gain moves +0.1%, iout_ratio +0.5%: both inside the 2% floor
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_connection_removed": BELOW_MEASURES},
                       below_ruling(measure="gain", delta=0.001))
    code, out = run_gate(ws, capsys)
    assert code == 1 and not errors(out), out


# gain moves +3%: past the 2% floor, inside 3 x its own mc sigma of 5%
NOISY_GAIN = {"iout_ratio": 2.01, "gain": 10.3}


def test_a_noisy_measure_inside_its_own_sigma_passes(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_connection_removed": NOISY_GAIN},
                       below_ruling(sigmas={"gain": 0.05}))
    code, out = run_gate(ws, capsys)
    assert code == 1 and not errors(out), out
    assert out["below_spread_ids"] == ["xmref_connection_removed"]


def test_another_measure_outside_the_floor_without_its_sigma_is_refused(tmp_path, monkeypatch, capsys):
    # same run, no sigmas: gain +3% against the 2% floor
    err = _refused(tmp_path, monkeypatch, capsys, below_ruling(),
                   measures=NOISY_GAIN)
    assert "'gain' moved" in err and "outside its own" in err


def test_a_sigma_too_small_to_cover_the_other_measure_is_refused(tmp_path, monkeypatch, capsys):
    # gain sigma 0.004: limit max(0.012, 0.02) = 0.02 < 0.03
    err = _refused(tmp_path, monkeypatch, capsys,
                   below_ruling(sigmas={"gain": 0.004}), measures=NOISY_GAIN)
    assert "'gain' moved" in err


def test_sigmas_naming_an_unbound_measure_is_refused(tmp_path, monkeypatch, capsys):
    err = _refused(tmp_path, monkeypatch, capsys,
                   below_ruling(sigmas={"f": 0.05}), measures=NOISY_GAIN)
    assert "sigmas names 'f'" in err


def test_a_below_spread_ruling_for_a_killed_mutant_is_refused(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch, {}, below_ruling())
    code, out = run_gate(ws, capsys)
    assert code == 2, out
    assert "kills" in out["error"]


# ---------------------------------------------------- sensitivity kills
# The bench's bounds must equal the spec's (speclib), so a mutant that moves
# a measure a long way but stays inside the spec cannot be killed by a
# bound. A declared `sensitivity` kills it against the unmutated design's
# own tt value instead, without the bound moving.

def with_sensitivity(ws: Path, **sens) -> None:
    bounds = [{"measure": "iout_ratio", "min": 1.8, "max": 2.2},
              {"measure": "gain", "min": 9.0, "max": 11.0}]
    for b in bounds:
        if b["measure"] in sens:
            b["sensitivity"] = sens[b["measure"]]
    (ws / "tb" / "mirror_tb.bounds.json").write_text(json.dumps(bounds),
                                                     encoding="utf-8")


# xmref_size_doubled moves iout_ratio +5% - still inside the 1.8..2.2 spec
IN_SPEC_5PC = {"iout_ratio": 2.1, "gain": 10.0}


def test_an_in_spec_move_past_the_sensitivity_is_a_kill(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_size_doubled_w": IN_SPEC_5PC}, None)
    with_sensitivity(ws, iout_ratio=0.03)
    code, out = run_gate(ws, capsys)
    assert code == 0 and out["survived"] == 0, out
    assert out["killed_by_sensitivity"] == 1
    assert out["mutants"]["xmref_size_doubled_w"]["sensitivity_kill"] == {
        "bench": "mirror_tb.cir", "measure": "iout_ratio", "delta": 0.05,
        "sensitivity": 0.03}


def test_without_a_sensitivity_the_same_move_still_survives(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_size_doubled_w": IN_SPEC_5PC}, None)
    code, out = run_gate(ws, capsys)
    assert code == 1 and out["survived"] == 1, out
    assert [v["kind"] for v in errors(out)] == ["survivor_size_doubled"]
    assert out["killed_by_sensitivity"] == 0


def test_a_move_inside_the_sensitivity_still_survives(tmp_path, monkeypatch, capsys):
    # +1% on iout_ratio, gain untouched: inside 3%, so not told apart
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_size_doubled_w": {"iout_ratio": 2.02,
                                               "gain": 10.0}}, None)
    with_sensitivity(ws, iout_ratio=0.03, gain=0.03)
    code, out = run_gate(ws, capsys)
    assert code == 1 and out["survived"] == 1, out
    assert [v["kind"] for v in errors(out)] == ["survivor_size_doubled"]
    assert "sensitivity_kill" not in out["mutants"]["xmref_size_doubled_w"]


def test_a_move_in_an_undeclared_measure_does_not_kill(tmp_path, monkeypatch, capsys):
    # gain moves 5% but only iout_ratio declares a sensitivity
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_size_doubled_w": {"iout_ratio": 2.0,
                                               "gain": 10.5}}, None)
    with_sensitivity(ws, iout_ratio=0.03)
    code, out = run_gate(ws, capsys)
    assert code == 1 and out["survived"] == 1, out


def test_a_sensitivity_below_the_spread_floor_is_refused(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_size_doubled_w": IN_SPEC_5PC}, None)
    with_sensitivity(ws, iout_ratio=0.01)
    code, out = run_gate(ws, capsys)
    assert code == 2 and "floor" in out["error"], out


def test_a_sensitivity_on_a_bound_not_scored_at_tt_is_refused(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_size_doubled_w": IN_SPEC_5PC}, None)
    (ws / "tb" / "mirror_tb.bounds.json").write_text(json.dumps([
        {"measure": "iout_ratio", "min": 1.8, "max": 2.2},
        {"measure": "gain", "min": 9.0, "max": 11.0, "corners": ["ss"],
         "sensitivity": 0.03}]), encoding="utf-8")
    code, out = run_gate(ws, capsys)
    assert code == 2 and "not scored at tt" in out["error"], out


def test_a_below_spread_ruling_for_a_sensitivity_kill_is_stale(tmp_path, monkeypatch, capsys):
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_connection_removed": IN_SPEC_5PC},
                       below_ruling(delta=0.05, sigma=0.02))
    with_sensitivity(ws, iout_ratio=0.03)
    code, out = run_gate(ws, capsys)
    assert code == 2 and "kills" in out["error"], out


def test_an_unmutated_design_meeting_spec_passes_with_sensitivities(tmp_path, monkeypatch, capsys):
    # the real deck path (fake ngspice): the baseline meets spec and is
    # never failed by a sensitivity; every real mutant is still killed
    ws = make_ws(tmp_path)
    (ws / "tb" / "mirror_tb.bounds.json").write_text(json.dumps([
        {"measure": "iout_ratio", "min": 1.8, "max": 2.2,
         "sensitivity": 0.02}]), encoding="utf-8")
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        make_reference_check_fake_eda(tmp_path))
    code, out = run_gate(ws, capsys)
    assert code == 0 and out["survived"] == 0, out


def test_a_sensitivity_on_a_near_zero_measure_is_refused(tmp_path, monkeypatch, capsys):
    # gain's unmutated value 0.1 is under 2% of its 11.0 bound: a relative
    # move there is simulator tolerance, not a mutant
    monkeypatch.setitem(BASE, "gain", 0.1)
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_size_doubled_w": IN_SPEC_5PC}, None)
    with_sensitivity(ws, gain=0.03)
    code, out = run_gate(ws, capsys)
    assert code == 2 and "near zero" in out["error"], out


# ------------------------------------- below_spread and near-zero measures
# A measure whose unmutated tt value sits near zero (under 2% of its bound's
# magnitude - the rule check_sensitivity_baselines refuses a sensitivity by)
# has a meaningless relative move, so a below_spread ruling is not refused
# for it. A measure that is not near zero is still held to its spread.

def with_v_low(ws: Path, monkeypatch, base_v_low: float) -> None:
    (ws / "tb" / "mirror_tb.bounds.json").write_text(json.dumps([
        {"measure": "iout_ratio", "min": 1.8, "max": 2.2},
        {"measure": "gain", "min": 9.0, "max": 11.0},
        {"measure": "v_low", "max": 0.2}]), encoding="utf-8")
    monkeypatch.setitem(BASE, "v_low", base_v_low)


def test_a_near_zero_measure_moving_a_lot_does_not_refuse_below_spread(tmp_path, monkeypatch, capsys):
    # v_low 1 uV -> -51 uV is a -52x relative move, but 1 uV is under 2% of
    # its 0.2 V bound; iout_ratio +0.5% sits inside its spread
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_connection_removed":
                        {**BELOW_MEASURES, "v_low": -51e-6}},
                       below_ruling())
    with_v_low(ws, monkeypatch, 1e-6)
    code, out = run_gate(ws, capsys)
    assert code == 1 and not errors(out), out
    assert out["below_spread_ids"] == ["xmref_connection_removed"]
    assert out["mutants"]["xmref_connection_removed"]["deltas"]["v_low"] == -52.0


def test_the_same_move_on_a_measure_not_near_zero_is_still_refused(tmp_path, monkeypatch, capsys):
    # v_low's baseline 0.1 V is half its 0.2 V bound: a +10% move is real
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_connection_removed":
                        {**BELOW_MEASURES, "v_low": 0.11}},
                       below_ruling())
    with_v_low(ws, monkeypatch, 0.1)
    code, out = run_gate(ws, capsys)
    assert code == 2 and "'v_low' moved" in out["error"], out


def test_a_near_zero_measure_crossing_its_bound_still_kills(tmp_path, monkeypatch, capsys):
    # the exemption only covers survivors: a mutant whose near-zero v_low
    # crosses its bound is killed, so a below_spread ruling on it is stale
    ws = make_ruled_ws(tmp_path, monkeypatch,
                       {"xmref_connection_removed":
                        {**BELOW_MEASURES, "v_low": 0.3}},
                       below_ruling())
    with_v_low(ws, monkeypatch, 1e-6)
    faked = check_bench_strength.run_mutant

    def bound_checked(eda_bin, ws_, mutant, *args, **kw):
        res = faked(eda_bin, ws_, mutant, *args, **kw)
        if (res["measures"].get("v_low") or 0.0) > 0.2:
            res["violations"] = [{"kind": "bound_violation",
                                  "severity": "error", "refs": ["v_low"]}]
        return res

    monkeypatch.setattr(check_bench_strength, "run_mutant", bound_checked)
    code, out = run_gate(ws, capsys)
    assert code == 2 and "kills" in out["error"], out


def test_near_zero_measures_needs_every_bench_to_agree():
    bounds = [{"measure": "v_low", "max": 0.2}]
    tt = {"a.cir": {"v_low"}, "b.cir": {"v_low"}}
    both = {"a.cir": {"v_low": 1e-6}, "b.cir": {"v_low": 1e-6}}
    one = {"a.cir": {"v_low": 1e-6}, "b.cir": {"v_low": 0.1}}
    by_bench = {"a.cir": bounds, "b.cir": bounds}
    nz = check_bench_strength.near_zero_measures
    assert nz(by_bench, both, tt) == {"v_low"}
    assert nz(by_bench, one, tt) == set()
