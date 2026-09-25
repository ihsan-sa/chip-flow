"""engine/lib/layoutlib.py: pure-Python pieces (docs/design.md 1.5, 5,
"### M9.") - the klayout .lyrdb parser, the DRC layer-name heuristic, the
ngspice print-line parser, and the refusal paths that do not need the eda
image at all. Real-tool coverage (run_klayout_drc, run_magic_extract,
run_netgen_lvs, run_ngspice actually launching bin/eda) lives in
tests/test_check_analog_drc.py etc., marked `slow`."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "lib"))

import layoutlib  # noqa: E402


def test_drc_layer_of_known_prefixes():
    assert layoutlib.drc_layer_of("DF.14_LV") == "comp"
    assert layoutlib.drc_layer_of("CO.10") == "contact"
    assert layoutlib.drc_layer_of("PL.6") == "poly2"
    assert layoutlib.drc_layer_of("NP.5a") == "nplus"
    assert layoutlib.drc_layer_of("PP.3d") == "pplus"
    assert layoutlib.drc_layer_of("NW.2b") == "nwell"
    assert layoutlib.drc_layer_of("metal1_OFFGRID") == "offgrid"
    assert layoutlib.drc_layer_of("MET1.1") == "metal1"
    assert layoutlib.drc_layer_of("something_unknown") == "unknown"


RDB_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<report-database>
 <categories>
  <category>
   <name>'DF.14_LV'</name>
   <description>DF.14_LV : some rule text</description>
  </category>
 </categories>
 <cells>
  <cell><name>'mirror'</name></cell>
 </cells>
 <items>
  <item>
   <category>'DF.14_LV'</category>
   <cell>'mirror'</cell>
   <values>
    <value>polygon: (0,0;0,1;1,1;1,0)</value>
   </values>
  </item>
 </items>
</report-database>
"""


def test_parse_drc_rdb_reads_items_and_categories(tmp_path):
    rdb = tmp_path / "report.lyrdb"
    rdb.write_text(RDB_TEMPLATE, encoding="utf-8")
    items = layoutlib.parse_drc_rdb(rdb)
    assert len(items) == 1
    assert items[0]["category"] == "DF.14_LV"
    assert items[0]["cell"] == "mirror"
    assert items[0]["description"] == "DF.14_LV : some rule text"
    assert items[0]["value"].startswith("polygon:")


EMPTY_RDB = """<?xml version="1.0" encoding="utf-8"?>
<report-database>
 <categories>
  <category>
   <name>'DF.14_LV'</name>
   <description>DF.14_LV : some rule text</description>
  </category>
 </categories>
 <cells>
  <cell><name>'mirror'</name></cell>
 </cells>
 <items>
 </items>
</report-database>
"""


def test_parse_drc_rdb_zero_items_is_a_real_clean_report(tmp_path):
    rdb = tmp_path / "clean.lyrdb"
    rdb.write_text(EMPTY_RDB, encoding="utf-8")
    assert layoutlib.parse_drc_rdb(rdb) == []


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def fake_pdk(tmp_path, monkeypatch, tech=" calma MET1RES 110 11\n"):
    pdk = tmp_path / "pdk" / "gf180mcuD"
    deck = pdk / layoutlib.DRC_DECK_REL
    deck.parent.mkdir(parents=True)
    deck.write_text("# fake deck\n", encoding="utf-8")
    magic = pdk / "libs.tech" / "magic" / "gf180mcuD.tech"
    magic.parent.mkdir(parents=True)
    magic.write_text(tech, encoding="utf-8")
    setup = pdk / "libs.tech" / "netgen" / "gf180mcuD_setup.tcl"
    setup.parent.mkdir(parents=True)
    setup.write_text("# fake setup\n", encoding="utf-8")
    monkeypatch.setattr(layoutlib, "pdk_root", lambda: pdk)
    return pdk


def test_run_eda_missing_launcher_is_a_layout_error(tmp_path, monkeypatch):
    monkeypatch.setattr(layoutlib, "EDA_BIN", tmp_path / "no-such-eda")
    with pytest.raises(layoutlib.LayoutError, match="failed to launch"):
        layoutlib.run_eda(["klayout", "-v"])


def test_run_klayout_drc_refuses_when_no_drc_result_banner(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc(
        "some unrelated klayout output, no banner"))
    with pytest.raises(layoutlib.LayoutError, match="DRC RESULT"):
        layoutlib.run_klayout_drc(tmp_path / "x.gds", "top", tmp_path / "out.lyrdb")


def test_run_klayout_drc_refuses_when_deck_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(layoutlib, "pdk_root", lambda: tmp_path)
    with pytest.raises(layoutlib.LayoutError, match="no klayout GF180 DRC deck"):
        layoutlib.run_klayout_drc(tmp_path / "x.gds", "top", tmp_path / "out.lyrdb")


def test_run_klayout_drc_nonzero_exit_refuses_despite_banner(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    rdb = tmp_path / "out.lyrdb"

    def crashed(*a, **k):
        rdb.write_text(EMPTY_RDB, encoding="utf-8")
        return FakeProc("DRC RESULT: SUCCESS (0 violations)", "NameError", 1)

    monkeypatch.setattr(layoutlib, "run_eda", crashed)
    with pytest.raises(layoutlib.LayoutError, match="exited 1"):
        layoutlib.run_klayout_drc(tmp_path / "x.gds", "top", rdb)


def test_run_klayout_drc_does_not_reread_a_stale_report(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    rdb = tmp_path / "out.lyrdb"
    rdb.write_text(EMPTY_RDB, encoding="utf-8")  # an earlier clean run's
    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc(
        "DRC RESULT: SUCCESS (0 violations)"))
    with pytest.raises(layoutlib.LayoutError, match="no report"):
        layoutlib.run_klayout_drc(tmp_path / "x.gds", "top", rdb)


def test_klayout_drc_variant_is_the_pdk_name(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    seen = {}
    rdb = tmp_path / "out.lyrdb"

    def fake(args, **k):
        seen["args"] = args
        rdb.write_text(EMPTY_RDB, encoding="utf-8")
        return FakeProc("DRC RESULT: SUCCESS (0 violations)")

    monkeypatch.setattr(layoutlib, "run_eda", fake)
    layoutlib.run_klayout_drc(tmp_path / "x.gds", "top", rdb)
    assert "variant=gf180mcuD" in seen["args"]
    assert f"decks={layoutlib.DRC_DECKS}" in seen["args"]


def test_run_magic_extract_refuses_when_no_spice_written(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    (tmp_path / "top.spice").write_text(".subckt top\n.ends\n")  # stale
    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc(
        "magic ran, wrote nothing useful"))
    with pytest.raises(layoutlib.LayoutError, match="top.spice"):
        layoutlib.run_magic_extract(tmp_path, tmp_path / "x.gds", "top",
                                    parasitics=False)
    assert not (tmp_path / "top.spice").exists()


def test_run_magic_extract_nonzero_exit_refuses(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)

    def died(*a, **k):
        (tmp_path / "top.pex.spice").write_text(".subckt top\n.ends\n")
        return FakeProc("", "segfault", 139)

    monkeypatch.setattr(layoutlib, "run_eda", died)
    with pytest.raises(layoutlib.LayoutError, match="exited 139"):
        layoutlib.run_magic_extract(tmp_path, tmp_path / "x.gds", "top",
                                    parasitics=True)


def test_parasitic_extraction_sets_thresholds_after_ext2spice_lvs(tmp_path, monkeypatch):
    # `ext2spice lvs` resets cthresh/rthresh to infinity: set after it, or
    # the "parasitic" netlist is the LVS one byte for byte (the review's
    # finding)
    fake_pdk(tmp_path, monkeypatch)
    seen = {}

    def fake(args, cwd=None, timeout=None, stdin_text=None):
        seen["tcl"] = stdin_text.splitlines()
        (tmp_path / "top.pex.spice").write_text(".subckt top\n.ends\n")
        return FakeProc()

    monkeypatch.setattr(layoutlib, "run_eda", fake)
    layoutlib.run_magic_extract(tmp_path, tmp_path / "x.gds", "top",
                                parasitics=True)
    tcl = seen["tcl"]
    lvs = tcl.index("ext2spice lvs")
    assert tcl.index("ext2spice cthresh 0") > lvs
    assert tcl.index("ext2spice extresist on") > lvs
    assert tcl.index("extresist all") < lvs


def test_magic_tech_fixes_the_rm1_input_typo(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch,
             tech=" calma MET1 34 0\n calma MET2RES 110 11\n"
                  " calma MET2RES 110 12\n")
    out = layoutlib.magic_tech(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert " calma MET1RES 110 11\n" in text
    assert " calma MET2RES 110 12\n" in text
    assert " calma MET2RES 110 11\n" not in text


def test_magic_tech_leaves_a_fixed_pdk_alone(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    assert layoutlib.magic_tech(tmp_path) is None


def test_magic_tech_refuses_an_unknown_rm1_mapping(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch, tech=" calma MET1 34 0\n")
    with pytest.raises(layoutlib.LayoutError, match="110/11"):
        layoutlib.magic_tech(tmp_path)


def test_count_parasitics():
    text = (".subckt top a b\nX0 a b rm1\nR0 a a.t0 1.5\nC0 a b 1f\n"
            "C1 b 0 2f\n.ends\n")
    assert layoutlib.count_parasitics(text) == {"r": 1, "c": 2}


def test_run_netgen_lvs_refuses_when_no_log_written(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc("netgen ran"))
    with pytest.raises(layoutlib.LayoutError, match="no log"):
        layoutlib.run_netgen_lvs(tmp_path, "a.spice", "a", "b.spice", "b",
                                 "out.log")


def test_run_netgen_lvs_does_not_reread_a_stale_match(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    (tmp_path / "out.log").write_text(
        "Final result: Circuits match uniquely.\n", encoding="utf-8")
    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc())
    with pytest.raises(layoutlib.LayoutError, match="no log"):
        layoutlib.run_netgen_lvs(tmp_path, "a.spice", "a", "b.spice", "b",
                                 "out.log")


def test_run_netgen_lvs_uses_the_pdk_setup(tmp_path, monkeypatch):
    pdk = fake_pdk(tmp_path, monkeypatch)
    seen = {}

    def fake(args, **k):
        seen["cmd"] = args[-1]
        (tmp_path / "out.log").write_text(
            "Final result: Circuits match uniquely.\n", encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(layoutlib, "run_eda", fake)
    layoutlib.run_netgen_lvs(tmp_path, "a.spice", "a", "b.spice", "b", "out.log")
    assert str(pdk / "libs.tech" / "netgen" / "gf180mcuD_setup.tcl") in seen["cmd"]
    assert "{}" not in seen["cmd"]


def netgen_writing(tmp_path, text, rc=0):
    def fake(*a, **k):
        (tmp_path / "out.log").write_text(text, encoding="utf-8")
        return FakeProc("", "", rc)
    return fake


def test_run_netgen_lvs_property_errors_are_not_a_match(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    monkeypatch.setattr(layoutlib, "run_eda", netgen_writing(
        tmp_path, "Netlists match uniquely with port and property errors.\n"
        "Final result: Circuits match uniquely.\n"
        "The following cells had property errors:\n"))
    matched, _text = layoutlib.run_netgen_lvs(
        tmp_path, "a.spice", "a", "b.spice", "b", "out.log")
    assert matched is False


def test_run_netgen_lvs_final_line_decides(tmp_path, monkeypatch):
    # a subcell's "Circuits match uniquely" above a failing top cell
    fake_pdk(tmp_path, monkeypatch)
    monkeypatch.setattr(layoutlib, "run_eda", netgen_writing(
        tmp_path, "Final result: Circuits match uniquely.\n"
        "Final result: Top level cell failed pin matching.\n"))
    matched, _text = layoutlib.run_netgen_lvs(
        tmp_path, "a.spice", "a", "b.spice", "b", "out.log")
    assert matched is False


def test_run_netgen_lvs_nonzero_exit_refuses(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    monkeypatch.setattr(layoutlib, "run_eda", netgen_writing(
        tmp_path, "Final result: Circuits match uniquely.\n", rc=1))
    with pytest.raises(layoutlib.LayoutError, match="exited 1"):
        layoutlib.run_netgen_lvs(tmp_path, "a.spice", "a", "b.spice", "b",
                                 "out.log")


def test_run_netgen_lvs_clean_match(tmp_path, monkeypatch):
    fake_pdk(tmp_path, monkeypatch)
    monkeypatch.setattr(layoutlib, "run_eda", netgen_writing(
        tmp_path, "Final result: Circuits match uniquely.\n"))
    matched, _text = layoutlib.run_netgen_lvs(
        tmp_path, "a.spice", "a", "b.spice", "b", "out.log")
    assert matched is True


def test_run_ngspice_nonzero_exit_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc(
        "v(iout) = 2.8\n", "Error: unknown subckt", 1))
    with pytest.raises(layoutlib.LayoutError, match="exited 1"):
        layoutlib.run_ngspice(tmp_path / "tb.cir")


def test_run_ngspice_engine_failure_with_exit_zero_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc(
        "vout = 0.66\n", "Warning: singular matrix:  check node x.n1\n"))
    with pytest.raises(layoutlib.LayoutError, match="singular_matrix"):
        layoutlib.run_ngspice(tmp_path / "tb.cir")


def test_parse_ngspice_prints_reads_name_equals_value_lines():
    output = """\
Note: some banner
v(iref) = 1.215070e+00
v(iout) = 2.825339e+00
not a measure line
"""
    values = layoutlib.parse_ngspice_prints(output)
    assert values["v(iref)"] == 1.21507
    assert values["v(iout)"] == 2.825339


def test_psub_tap_pad_center_matches_pad_center_helper():
    # gf180_cells()'s own import activates a gdsfactory PDK as a side
    # effect - psub_tap() is always called after it in real generator code
    # (gen_mirror.py etc.), never standalone; matched here so this stays a
    # test of psub_tap()'s own geometry, not of PDK activation order.
    layoutlib.gf180_cells()
    tap = layoutlib.psub_tap(size=1.0)
    cx, cy = layoutlib.pad_center(1.0)
    assert (cx, cy) == (0.5, 0.5)
    polys = tap.get_polygons(by_spec=True)
    assert layoutlib.GF180_LAYER["metal1"] in polys
    assert layoutlib.GF180_LAYER["contact"] in polys


class _Recorder:
    """Stands in for a gf.Component: rect() only calls add_polygon."""

    def __init__(self):
        self.polys = []

    def add_polygon(self, pts, layer):
        self.polys.append((layer, pts))


def _bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def test_via1_is_a_centred_cut_enclosed_on_both_metals():
    top = _Recorder()
    layoutlib.via1(top, 1.0, 2.0)
    by_layer = {layer: _bbox(pts) for layer, pts in top.polys}
    L = layoutlib.GF180_LAYER
    assert set(by_layer) == {L["via1"], L["metal1"], L["metal2"]}
    h = layoutlib.VIA1_SIZE / 2
    assert by_layer[L["via1"]] == pytest.approx((1 - h, 2 - h, 1 + h, 2 + h))
    e = h + layoutlib.VIA1_ENC
    for m in ("metal1", "metal2"):
        assert by_layer[L[m]] == pytest.approx((1 - e, 2 - e, 1 + e, 2 + e))


def test_nwell_tap_is_nplus_and_draws_no_well():
    pytest.importorskip("gdsfactory")
    L = layoutlib.GF180_LAYER
    ntap = layoutlib.nwell_tap(1.0)
    layers = {tuple(li) for li in ntap.layers}
    assert L["nplus"] in layers and L["pplus"] not in layers
    assert L["nwell"] not in layers
    assert {L["comp"], L["contact"], L["metal1"]} <= layers


# what this box's ngspice printed for a .control `meas` whose target never
# happened (corpus/ade/comparator's pex bench: outn never fell)
MEAS_NEVER_MET = (
    "vdiff_pos           =  -3.30000e+00\n"
    "\nError: measure  tdelay  trig(TARG) : out of interval\n"
    " meas tran tdelay trig v(clk) val=1.4 rise=1 targ v(outn) val=1.4 "
    "fall=1 failed!\n\n"
    "Warning from checkvalid: vector tdelay is not available or has zero "
    "length.\n")


def test_run_ngspice_measure_never_met_is_still_a_refusal_by_default(
        tmp_path, monkeypatch):
    monkeypatch.setattr(layoutlib, "run_eda",
                        lambda *a, **k: FakeProc(MEAS_NEVER_MET))
    with pytest.raises(layoutlib.LayoutError, match="ngspice_error"):
        layoutlib.run_ngspice(tmp_path / "tb.cir")


def test_run_ngspice_hands_a_measure_never_met_back_by_name(tmp_path,
                                                            monkeypatch):
    monkeypatch.setattr(layoutlib, "run_eda",
                        lambda *a, **k: FakeProc(MEAS_NEVER_MET))
    failed = set()
    out = layoutlib.run_ngspice(tmp_path / "tb.cir", failed_measures=failed)
    assert failed == {"tdelay"}
    assert layoutlib.parse_ngspice_prints(out)["vdiff_pos"] == -3.3


def test_run_ngspice_measure_never_met_does_not_hide_an_engine_error(
        tmp_path, monkeypatch):
    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc(
        MEAS_NEVER_MET + "Error: unknown subckt: x1 a b foo\n"))
    with pytest.raises(layoutlib.LayoutError, match="unknown_subckt"):
        layoutlib.run_ngspice(tmp_path / "tb.cir", failed_measures=set())
