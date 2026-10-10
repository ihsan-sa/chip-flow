"""engine/scripts/check_spec_lint_ade.py: the CLI/report contract around
speclib.lint_spec_ade plus this script's own tb/*.bounds.json reconciliation
(docs/design.md 1.1's script contract, 1.5's ade spec_lint row). Hermetic:
no eda/ngspice call, tb/*.bounds.json is read straight off disk."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_spec_lint_ade  # noqa: E402

GOOD_YAML = """\
top: current_mirror
supply: {vdd: 3.3}
devices: [xmref, xmout]
corners: default
measures:
  - name: iout_ratio
    bounds: {min: 1.8, max: 2.2}
"""


def make_ws(tmp_path: Path, spec_text: str = GOOD_YAML) -> Path:
    ws = tmp_path / "ws"
    (ws / "spec").mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(spec_text, encoding="utf-8")
    return ws


def write_bounds(ws: Path, bench_stem: str, entries: list[dict]) -> None:
    (ws / "tb").mkdir(parents=True, exist_ok=True)
    (ws / "tb" / f"{bench_stem}.cir").write_text("* bench\n", encoding="utf-8")
    (ws / "tb" / f"{bench_stem}.bounds.json").write_text(
        json.dumps(entries), encoding="utf-8")


def test_clean_spec_with_no_tb_yet_passes(tmp_path, capsys):
    # right after spec-writer runs, before any bench exists - nothing to
    # reconcile, and no error either.
    ws = make_ws(tmp_path)
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pass"


def test_matching_bench_bounds_passes(tmp_path, capsys):
    ws = make_ws(tmp_path)
    write_bounds(ws, "mirror_tb", [{"measure": "iout_ratio", "min": 1.8, "max": 2.2}])
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pass"


def test_spec_measure_with_no_bench_bound_fails(tmp_path, capsys):
    ws = make_ws(tmp_path)
    write_bounds(ws, "mirror_tb", [{"measure": "some_other_measure", "min": 0, "max": 1}])
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    kinds = {v["kind"] for v in out["violations"]}
    assert "measure_no_bench_bound" in kinds


def test_bench_bound_with_no_spec_measure_fails(tmp_path, capsys):
    ws = make_ws(tmp_path)
    write_bounds(ws, "mirror_tb", [
        {"measure": "iout_ratio", "min": 1.8, "max": 2.2},
        {"measure": "extra_measure", "min": 0, "max": 1},
    ])
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    kinds = {v["kind"] for v in out["violations"]}
    assert "bench_bound_no_spec_measure" in kinds


def test_missing_spec_yaml_is_an_error(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"


def test_spec_grid_that_spans_the_defaults_passes(tmp_path, capsys):
    ws = make_ws(tmp_path, GOOD_YAML.replace(
        "corners: default",
        "corners: {grid: {process: [typical, ff, ss], temp_c: [-40, 25, 125]}}"))
    assert check_spec_lint_ade.main(["--workspace", str(ws)]) == 0


def test_spec_grid_missing_ss_is_bad_corners(tmp_path, capsys):
    ws = make_ws(tmp_path, GOOD_YAML.replace(
        "corners: default",
        "corners: {grid: {process: [typical, ff], temp_c: [-40, 25, 125]}}"))
    assert check_spec_lint_ade.main(["--workspace", str(ws)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [v["kind"] for v in out["violations"]] == ["bad_corners"]


def test_measure_scoped_to_a_corner_the_grid_never_runs_is_refused(tmp_path, capsys):
    grid = "corners: {grid: {process: [typical, ff, ss], temp_c: [-40, 25, 125]}}"
    scoped = "    bounds: {min: 1.8, max: 2.2}\n    corners: [tt]\n"
    ws = make_ws(tmp_path, GOOD_YAML.replace("corners: default", grid)
                 .replace("    bounds: {min: 1.8, max: 2.2}\n", scoped))
    assert check_spec_lint_ade.main(["--workspace", str(ws)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [v["kind"] for v in out["violations"]] == ["measure_bad_corners"]

    ws2 = make_ws(tmp_path / "b", GOOD_YAML.replace(
        "    bounds: {min: 1.8, max: 2.2}\n", scoped))  # default five hold tt
    assert check_spec_lint_ade.main(["--workspace", str(ws2)]) == 0


def _scoped(corners: str) -> str:
    return GOOD_YAML.replace("    bounds: {min: 1.8, max: 2.2}\n",
                             "    bounds: {min: 1.8, max: 2.2}\n"
                             f"    corners: {corners}\n")


def test_measure_scoped_to_a_passive_corner_passes(tmp_path, capsys):
    """Red before: the lint expanded the spec with no passives, so a PLL
    measure scoped to [tt_pff] (a resistor netlist sweeps it) was refused
    and its bound went unscored. The lint reads the spec alone; a netlist
    that does not sweep tt_pff is sim_pvt's sim_bound_not_swept."""
    ws = make_ws(tmp_path, _scoped("[tt_pff]"))
    (ws / "netlist").mkdir()
    (ws / "netlist" / "pll.cir").write_text(
        "xr1 a b vss ppolyf_u_1k r_width=2e-6 r_length=1e-5\n",
        encoding="utf-8")
    assert check_spec_lint_ade.main(["--workspace", str(ws)]) == 0, \
        capsys.readouterr().out
    grid = ("corners: {grid: {process: [typical, ff, ss], "
            "temp_c: [-40, 25, 125]}}")
    ws2 = make_ws(tmp_path / "b", _scoped("[tt_pss]").replace(
        "corners: default", grid))
    assert check_spec_lint_ade.main(["--workspace", str(ws2)]) == 0, \
        capsys.readouterr().out


def test_measure_names_a_corner_by_its_alias(tmp_path, capsys):
    # names_of: the default tt corner is also tt_27c, as sim_pvt scores it
    ws = make_ws(tmp_path, _scoped("[tt_27c]"))
    assert check_spec_lint_ade.main(["--workspace", str(ws)]) == 0, \
        capsys.readouterr().out


@pytest.mark.parametrize("corners", ["[ss_pff]", "[tt_25c]", "[nosuch]"])
def test_measure_scoped_to_a_corner_no_sweep_runs_is_refused(
        tmp_path, capsys, corners):
    # ss_pff is swept only when the spec names it; tt_25c is a grid name
    # the default five never answer to
    ws = make_ws(tmp_path, _scoped(corners))
    assert check_spec_lint_ade.main(["--workspace", str(ws)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [v["kind"] for v in out["violations"]] == ["measure_bad_corners"]


def test_positive_footprint_passes(tmp_path, capsys):
    ws = make_ws(tmp_path, GOOD_YAML + "footprint_um: {width: 60, height: 60.5}\n")
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 0, capsys.readouterr().out


@pytest.mark.parametrize("fp", [
    "{width: 60}",                       # height missing
    "{width: 0, height: 60}",            # not positive
    "{width: -5, height: 60}",
    "{width: '60', height: 60}",         # not a number
    "{width: true, height: 60}",
    "{width: 60, height: 60, depth: 1}",  # unknown key
    "[60, 60]",                           # not a mapping
])
def test_bad_footprint_is_refused(tmp_path, capsys, fp):
    ws = make_ws(tmp_path, GOOD_YAML + f"footprint_um: {fp}\n")
    code = check_spec_lint_ade.main(["--workspace", str(ws)])
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert [v["kind"] for v in out["violations"]] == ["bad_footprint"]


def test_spec_grid_with_extra_passes_and_bad_extra_is_bad_corners(tmp_path, capsys):
    grid = "{process: [typical, ff, ss], temp_c: [-40, 25, 125]}"
    ws = make_ws(tmp_path, GOOD_YAML.replace(
        "corners: default", f"corners: {{grid: {grid}, extra: [ss_pff, ff_pss]}}"))
    assert check_spec_lint_ade.main(["--workspace", str(ws)]) == 0
    capsys.readouterr()
    for i, extra in enumerate(("[nope]", "1")):
        ws = make_ws(tmp_path / f"b{i}", GOOD_YAML.replace(
            "corners: default", f"corners: {{grid: {grid}, extra: {extra}}}"))
        assert check_spec_lint_ade.main(["--workspace", str(ws)]) == 1
        out = json.loads(capsys.readouterr().out)
        assert [v["kind"] for v in out["violations"]] == ["bad_corners"]
