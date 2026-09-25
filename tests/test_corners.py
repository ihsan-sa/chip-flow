"""engine/scripts/corners.py: PVT corner axes and the default sim_pvt sweep
(docs/design.md 1.4, 5, "### M8."). Hermetic (pure venv: yaml)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import corners  # noqa: E402
from checklib import CheckError  # noqa: E402

REAL_YAML = ENGINE / "reference" / "corners.yaml"


def test_real_corners_yaml_loads_and_validates():
    data = corners.load(REAL_YAML)
    names = [c["name"] for c in corners.default_corners(data)]
    assert names == ["tt", "ss", "ff", "sf", "fs"]


def test_default_set_is_five_and_never_fewer():
    # design.md 5: "the default is the four extremes with typical, never
    # fewer" - four non-typical process corners plus typical itself.
    data = corners.load(REAL_YAML)
    defaults = corners.default_corners(data)
    assert len(defaults) == 5
    assert {c["process"] for c in defaults} == {"typical", "ss", "ff", "sf", "fs"}


def test_tt_corner_is_nominal():
    data = corners.load(REAL_YAML)
    tt = corners.corners_by_name(data, ["tt"])[0]
    assert tt["process"] == "typical"
    assert tt["temp_c"] == 27
    assert tt["supply_pct"] == 0


def test_ss_pairs_hot_and_low_supply_the_named_pvt_fault():
    # gates.yaml's own fault for sim_pvt: "meets at typical, loses headroom
    # at slow and hot" - ss must be the hot, low-supply corner.
    data = corners.load(REAL_YAML)
    ss = corners.corners_by_name(data, ["ss"])[0]
    assert ss["temp_c"] == 125
    assert ss["supply_pct"] == -10


def test_resolve_vdd_applies_supply_pct():
    assert corners.resolve_vdd({"supply_pct": -10}, 3.3) == pytest.approx(2.97)
    assert corners.resolve_vdd({"supply_pct": 10}, 3.3) == pytest.approx(3.63)
    assert corners.resolve_vdd({"supply_pct": 0}, 3.3) == pytest.approx(3.3)


def test_unknown_corner_name_raises(tmp_path):
    data = corners.load(REAL_YAML)
    with pytest.raises(CheckError):
        corners.corners_by_name(data, ["nope"])


def test_missing_axis_raises(tmp_path):
    p = tmp_path / "corners.yaml"
    p.write_text("version: 1\nprocess: [typical]\n"
                 "temperature_c: [27]\nsupply_pct: [0]\n"
                 "default_corners: []\n", encoding="utf-8")
    with pytest.raises(CheckError):
        corners.load(p)


def test_default_corner_referencing_unknown_process_raises(tmp_path):
    p = tmp_path / "corners.yaml"
    p.write_text(
        "version: 1\nprocess: [typical]\ntemperature_c: [27]\n"
        "supply_pct: [0]\ndefault_corners:\n"
        "  - {name: tt, process: bogus, temp_c: 27, supply_pct: 0}\n",
        encoding="utf-8")
    with pytest.raises(CheckError):
        corners.load(p)


def test_cli_default_lists_five_corners(capsys):
    import json
    code = corners.main(["--corners-yaml", str(REAL_YAML)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(out["corners"]) == 5


def test_cli_names_filters_and_orders(capsys):
    import json
    code = corners.main(["--corners-yaml", str(REAL_YAML), "--names", "ff", "tt"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [c["name"] for c in out["corners"]] == ["ff", "tt"]


def test_cli_unknown_name_is_error_exit_2(capsys):
    code = corners.main(["--corners-yaml", str(REAL_YAML), "--names", "nope"])
    assert code == 2


# ------------------------------------------------------------ spec grids

DAC_GRID = {"process": ["typical", "ff", "ss"], "temp_c": [-40, 25, 125]}


def test_grid_expands_process_by_temp_at_nominal_supply():
    data = corners.load(REAL_YAML)
    got = corners.grid_corners(data, DAC_GRID)
    assert len(got) == 9
    assert {c["supply_pct"] for c in got} == {0}
    assert got[0] == {"name": "tt_m40c", "process": "typical", "temp_c": -40,
                      "supply_pct": 0}
    assert {c["name"] for c in got} >= {"tt_25c", "ff_125c", "ss_m40c"}


def test_grid_supply_points_are_named():
    data = corners.load(REAL_YAML)
    got = corners.grid_corners(data, dict(DAC_GRID, supply_pct=[-10, 10]))
    assert {c["name"] for c in got} >= {"ss_125c_vm10", "ff_m40c_vp10"}


@pytest.mark.parametrize("grid, why", [
    ({"process": ["typical", "ff"], "temp_c": [-40, 125]}, "lacks ['ss']"),
    ({"process": ["typical", "ff", "ss"], "temp_c": [25, 125]}, "must include -40"),
    ({"process": ["typical", "ff", "ss"], "temp_c": [-40, 150]}, "outside"),
    ({"process": ["typical", "ff", "ss", "xx"], "temp_c": [-40, 125]}, "not in"),
    ({"process": ["typical", "ff", "ss"], "temp_c": [-40, 125],
      "supply_pct": [20]}, "outside"),
    ({"process": ["typical", "ff", "ss"], "temp_c": [-40, 125], "vdd": [3.3]},
     "unknown key"),
])
def test_grid_that_does_not_span_the_defaults_is_refused(grid, why):
    with pytest.raises(CheckError, match=why.replace("[", r"\[").replace("]", r"\]")):
        corners.grid_corners(corners.load(REAL_YAML), grid)


def test_spec_corners_forms():
    data = corners.load(REAL_YAML)
    names = lambda field: [c["name"] for c in corners.spec_corners(data, field)]
    assert names("default") == ["tt", "ss", "ff", "sf", "fs"]
    assert names(["tt"]) == ["tt", "ss", "ff", "sf", "fs"]  # never fewer
    assert len(names("all")) == 5 * 3 * 3
    assert len(names({"grid": DAC_GRID})) == 9
    with pytest.raises(CheckError):
        corners.spec_corners(data, {"grid": DAC_GRID, "extra": 1})
