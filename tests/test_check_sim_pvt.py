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


def test_a_tt_only_bench_is_not_run_at_the_other_corners(tmp_path, monkeypatch,
                                                        capsys):
    # every bound scoped to tt: ss would fail if it ran, so a pass here
    # means it did not, and the report says where it was not scored
    ws = make_ws(tmp_path)
    (ws / "tb" / "mirror_tb.bounds.json").write_text(json.dumps(
        [{"measure": "iout_ratio", "min": 1.95, "max": 2.05,
          "corners": ["tt"]}]), encoding="utf-8")
    monkeypatch.setattr(sim_run, "EDA_BIN", make_corner_sensitive_fake_eda(tmp_path))
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert [r["corner"] for r in out["results"]] == ["tt"]
    assert sorted(n["corner"] for n in out["not_scored"]) == [
        "ff", "fs", "sf", "ss"]


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


def test_spec_grid_replaces_the_default_five_at_fixed_vdd(tmp_path, monkeypatch, capsys):
    # The R-2R DAC shakedown's brief asked for tt/ff/ss x -40/25/125 C at a
    # fixed 3.3 V; before the grid form, sim_pvt could only run the default
    # five, whose ss/ff move the supply by 10 %.
    ws = make_ws(tmp_path, corners_field=(
        "{grid: {process: [typical, ff, ss], temp_c: [-40, 25, 125]}}"))
    eda = make_corner_sensitive_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert out["corners"] == [f"{p}_{t}c" for p in ("tt", "ff", "ss")
                              for t in ("m40", "25", "125")]
    assert {r["vdd"] for r in out["results"]} == {"3.3"}
    assert {r["temp_c"] for r in out["results"]} == {-40, 25, 125}
    assert code == 1, out  # the ss rows still fail, all three of them
    bad = {v["refs"][1] for v in out["violations"] if v["kind"] == "sim_bound_fail"}
    assert bad == {"ss_m40c", "ss_25c", "ss_125c"}


def test_spec_without_a_grid_keeps_the_default_five(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path, corners_field="default")
    monkeypatch.setattr(sim_run, "EDA_BIN", make_corner_sensitive_fake_eda(tmp_path))
    check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert out["corners"] == ["tt", "ss", "ff", "sf", "fs"]


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


# --- passive spread: poly resistor and MIM cap corners ----------------------

PASSIVE_CORNERS = {"tt_pss", "tt_pff"}


def _passive_ws(tmp_path: Path, device_line: str, lib_line: str) -> Path:
    ws = make_ws(tmp_path)
    (ws / "netlist" / "mirror.cir").write_text(
        f"* netlist\n{device_line}\n", encoding="utf-8")
    (ws / "tb" / "mirror_tb.cir").write_text(
        BENCH_TEMPLATE.replace(".temp", lib_line + "\n.temp"),
        encoding="utf-8")
    return ws


def _fake_eda_failing_on(tmp_path: Path, needle: str) -> Path:
    """Like make_corner_sensitive_fake_eda, but the out-of-bounds ratio
    comes back only for a deck holding `needle` - a passive section."""
    eda = make_corner_sensitive_fake_eda(tmp_path)
    eda.write_text(eda.read_text(encoding="utf-8").replace(
        "sm141064.spice' ss\"", f"sm141064.spice' {needle}\""),
        encoding="utf-8")
    assert f"' {needle}\"" in eda.read_text(encoding="utf-8")
    return eda


def test_resistor_design_sweeps_the_passive_corners(tmp_path, monkeypatch,
                                                    capsys):
    # Red before passive corners: the sweep was the five MOS corners, R
    # moved only with them, and a loop filter that fails at typical
    # transistors with low R passed.
    ws = _passive_ws(tmp_path, "xr1 a b vss ppolyf_u_1k r_width=2e-6 "
                     "r_length=1e-5",
                     ".lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' "
                     "{{RES_CORNER}}")
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        _fake_eda_failing_on(tmp_path, "res_ff"))
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert set(out["corners"]) == {"tt", "ss", "ff", "sf", "fs"} | PASSIVE_CORNERS
    assert code == 1, out
    # res_ff: the ff corner (R with the transistors) and tt_pff
    failed = {r["corner"] for r in out["results"] if r["violations"]}
    assert failed == {"ff", "tt_pff"}, out
    passive = {r["corner"]: r["passive"] for r in out["results"]}
    assert passive["tt_pff"] == "ff" and passive["sf"] == "typical"


def test_a_spec_opts_into_the_passive_skew_corners(tmp_path, monkeypatch,
                                                   capsys):
    ws = _passive_ws(tmp_path, "xr1 a b vss ppolyf_u_1k r_width=2e-6 "
                     "r_length=1e-5",
                     ".lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' "
                     "{{RES_CORNER}}")
    spec = ws / "spec" / "spec.yaml"
    spec.write_text(spec.read_text() + "corners: [ss_pff, ff_pss]\n")
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        _fake_eda_failing_on(tmp_path, "res_ff"))
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert {"ss_pff", "ff_pss"} | PASSIVE_CORNERS <= set(out["corners"])
    failed = {r["corner"] for r in out["results"] if r["violations"]}
    assert code == 1 and failed == {"ff", "tt_pff", "ss_pff"}, out


def test_mim_bench_that_hardcodes_typical_is_a_finding(tmp_path, monkeypatch,
                                                      capsys):
    ws = _passive_ws(tmp_path, "xc1 a vss cap_mim_2f0fF c_width=1e-5 "
                     "c_length=1e-5",
                     ".lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' "
                     "mimcap_typical")
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        _fake_eda_failing_on(tmp_path, "no-such-section"))
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert PASSIVE_CORNERS <= set(out["corners"])
    assert code == 1, out
    kinds = [(v["kind"], v["refs"]) for v in out["violations"]]
    assert kinds == [("passive_corner_unselected", ["MIM_CORNER"])], out


def test_mim_bench_with_the_placeholder_passes(tmp_path, monkeypatch, capsys):
    ws = _passive_ws(tmp_path, "xc1 a vss cap_mim_2f0fF c_width=1e-5 "
                     "c_length=1e-5",
                     ".lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' "
                     "{{MIM_CORNER}}")
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        _fake_eda_failing_on(tmp_path, "no-such-section"))
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    decks = {r["corner"]: Path(r["deck"]).read_text() for r in out["results"]}
    assert "mimcap_ss" in decks["tt_pss"] and "mimcap_ff" in decks["tt_pff"]
    assert "mimcap_typical" in decks["fs"]


# --- a dimension the person scoped out at H1 --------------------------------

MIM_RULING = ("MIM capacitor spread is out of scope for this rung: MIM "
              "stays pinned typical, a known limit.")
MIM_CLAUSE = "MIM capacitor spread is out of scope for this rung"
RC_NETLIST = ("xr1 a b vss ppolyf_u_1k r_width=2e-6 r_length=1e-5\n"
              "xc1 a vss cap_mim_2f0fF c_width=1e-5 c_length=1e-5")
RC_LIBS = (".lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' {{RES_CORNER}}\n"
           ".lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' {{MIM_CORNER}}")


def _h1_scope_out(ws: Path, dimension="mim_cap", quote=MIM_CLAUSE) -> None:
    """The state.json record `state.py scope-out` leaves on an approved H1
    whose note carries the person's ruling."""
    (ws / "state.json").write_text(json.dumps({"human": {"H1": {
        "status": "approved", "answer": "approved H1-abc123",
        "note": MIM_RULING,
        "scope_out": [{"dimension": dimension, "quote": quote,
                       "pinned": "typical", "ts": "2026-09-27T05:00:00"}]}}}),
        encoding="utf-8")


def test_a_scoped_out_mim_corner_is_pinned_and_nothing_else(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    # Red before the route: sim_pvt read no ruling, so the MIM cap moved to
    # mimcap_ss/mimcap_ff with the passive and process corners whatever the
    # person had ruled at H1.
    ws = _passive_ws(tmp_path, RC_NETLIST, RC_LIBS)
    _h1_scope_out(ws)
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        _fake_eda_failing_on(tmp_path, "res_ff"))
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    # the resistor still brings its passive corners in, and they still gate
    assert set(out["corners"]) == {"tt", "ss", "ff", "sf", "fs"} | PASSIVE_CORNERS
    failed = {r["corner"] for r in out["results"] if r["violations"]}
    assert code == 1 and failed == {"ff", "tt_pff"}, out
    decks = {r["corner"]: Path(r["deck"]).read_text() for r in out["results"]}
    for name, deck in decks.items():
        assert "mimcap_typical" in deck and "mimcap_ss" not in deck \
            and "mimcap_ff" not in deck, name
    assert "res_ss" in decks["ss"] and "res_ff" in decks["tt_pff"]
    assert "sm141064.spice' ss\n" in decks["ss"]      # process still swept
    assert {r["corner"]: r["pinned"] for r in out["results"]}["ss"] \
        == {"mim_cap": "typical"}
    [so] = out["scoped_out"]
    assert so["dimension"] == "mim_cap" and so["quote"] == MIM_CLAUSE


def test_a_scoped_out_mim_bench_may_hard_code_typical(tmp_path, monkeypatch,
                                                      capsys):
    """The bench ring_osc_div's bench-writer wrote to the ruling: MIM pinned
    at mimcap_typical. Scoped out, it owes no {{MIM_CORNER}}; a MIM-only
    design sweeps just the five, since nothing else moves a passive."""
    ws = _passive_ws(tmp_path, "xc1 a vss cap_mim_2f0fF c_width=1e-5 "
                     "c_length=1e-5",
                     ".lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' "
                     "mimcap_typical")
    _h1_scope_out(ws)
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        _fake_eda_failing_on(tmp_path, "no-such-section"))
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert set(out["corners"]) == {"tt", "ss", "ff", "sf", "fs"}


def test_a_scope_out_the_h1_answer_does_not_make_is_an_error(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    ws = _passive_ws(tmp_path, RC_NETLIST, RC_LIBS)
    _h1_scope_out(ws, dimension="resistor")     # the answer rules on MIM only
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        _fake_eda_failing_on(tmp_path, "no-such-section"))
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and "does not name resistor" in out["error"], out
    assert "state.py" in out["remediation"]


# --- a bound scoped to a corner the sweep never runs -------------------------

RES_LINE = "xr1 a b vss ppolyf_u_1k r_width=2e-6 r_length=1e-5"
RES_LIB = ".lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' {{RES_CORNER}}"


def _with_pff_bound(ws: Path) -> None:
    """The PLL shape: one bound everywhere, one scoped to tt_pff."""
    (ws / "tb" / "mirror_tb.bounds.json").write_text(json.dumps(
        [{"measure": "iout_ratio", "min": 1.95, "max": 2.05},
         {"measure": "iout_ratio", "min": 1.0, "max": 3.0,
          "corners": ["tt_pff"]}]), encoding="utf-8")


def test_a_tt_pff_bound_on_a_resistor_netlist_is_scored(tmp_path,
                                                        monkeypatch, capsys):
    ws = _passive_ws(tmp_path, RES_LINE, RES_LIB)
    _with_pff_bound(ws)
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        _fake_eda_failing_on(tmp_path, "no-such-section"))
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert "tt_pff" in {r["corner"] for r in out["results"]}


@pytest.mark.parametrize("scoped_out", [False, True])
def test_a_bound_scoped_to_an_unswept_corner_is_refused(tmp_path, monkeypatch,
                                                        capsys, scoped_out):
    """spec_lint accepts [tt_pff] without reading the netlist. A netlist
    with no resistor, or a MIM-only one whose spread is scoped out, never
    sweeps tt_pff: before, the bench ran at the other corners, that bound
    was never scored and the gate passed."""
    if scoped_out:
        ws = _passive_ws(tmp_path, "xc1 a vss cap_mim_2f0fF c_width=1e-5 "
                         "c_length=1e-5",
                         ".lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' "
                         "mimcap_typical")
        _h1_scope_out(ws)
    else:
        ws = make_ws(tmp_path)
    _with_pff_bound(ws)
    monkeypatch.setattr(sim_run, "EDA_BIN",
                        _fake_eda_failing_on(tmp_path, "no-such-section"))
    code = check_sim_pvt.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert "tt_pff" not in out["corners"]
    assert [(v["kind"], v["refs"]) for v in out["violations"]] == [
        ("sim_bound_not_swept", ["iout_ratio"])], out
