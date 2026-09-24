"""engine/lib/layoutlib.py: pure-Python pieces (docs/design.md 1.5, 5,
"### M9.") - the klayout .lyrdb parser, the DRC layer-name heuristic, the
ngspice print-line parser, and the refusal paths that do not need the eda
image at all. Real-tool coverage (run_klayout_drc, run_magic_extract,
run_netgen_lvs, run_ngspice actually launching bin/eda) lives in
tests/test_check_analog_drc.py etc., marked `slow`."""
from __future__ import annotations

import sys
from pathlib import Path

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


def test_run_klayout_drc_refuses_when_no_drc_result_banner(tmp_path, monkeypatch):
    class FakeProc:
        stdout = "some unrelated klayout output, no banner"
        stderr = ""

    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc())
    monkeypatch.setattr(layoutlib, "pdk_root", lambda: tmp_path)
    (tmp_path / "libs.tech" / "klayout" / "tech" / "drc").mkdir(parents=True)
    deck = tmp_path / layoutlib.DRC_DECK_REL
    deck.write_text("# fake deck\n", encoding="utf-8")
    try:
        layoutlib.run_klayout_drc(tmp_path / "x.gds", "top", tmp_path / "out.lyrdb")
        assert False, "expected LayoutError"
    except layoutlib.LayoutError as exc:
        assert "DRC RESULT" in str(exc)


def test_run_klayout_drc_refuses_when_deck_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(layoutlib, "pdk_root", lambda: tmp_path)
    try:
        layoutlib.run_klayout_drc(tmp_path / "x.gds", "top", tmp_path / "out.lyrdb")
        assert False, "expected LayoutError"
    except layoutlib.LayoutError as exc:
        assert "no klayout GF180 DRC deck" in str(exc)


def test_run_magic_extract_refuses_when_no_spice_written(tmp_path, monkeypatch):
    class FakeProc:
        stdout = "magic ran, wrote nothing useful"
        stderr = ""

    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc())
    try:
        layoutlib.run_magic_extract(tmp_path, tmp_path / "x.gds", "top",
                                    parasitics=False)
        assert False, "expected LayoutError"
    except layoutlib.LayoutError as exc:
        assert "no .spice" not in str(exc)  # message names the real file
        assert "top.spice" in str(exc)


def test_run_netgen_lvs_refuses_when_no_log_written(tmp_path, monkeypatch):
    class FakeProc:
        stdout = "netgen ran"
        stderr = ""

    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc())
    try:
        layoutlib.run_netgen_lvs(tmp_path, "a.spice", "a", "b.spice", "b",
                                 "out.log")
        assert False, "expected LayoutError"
    except layoutlib.LayoutError as exc:
        assert "no log" in str(exc)


def test_run_netgen_lvs_property_errors_are_not_a_match(tmp_path, monkeypatch):
    class FakeProc:
        stdout = ""
        stderr = ""

    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc())
    (tmp_path / "out.log").write_text(
        "Final result: Circuits match uniquely.\nProperty errors were found.\n",
        encoding="utf-8")
    matched, _text = layoutlib.run_netgen_lvs(
        tmp_path, "a.spice", "a", "b.spice", "b", "out.log")
    assert matched is False


def test_run_netgen_lvs_clean_match(tmp_path, monkeypatch):
    class FakeProc:
        stdout = ""
        stderr = ""

    monkeypatch.setattr(layoutlib, "run_eda", lambda *a, **k: FakeProc())
    (tmp_path / "out.log").write_text(
        "Final result: Circuits match uniquely.\n", encoding="utf-8")
    matched, _text = layoutlib.run_netgen_lvs(
        tmp_path, "a.spice", "a", "b.spice", "b", "out.log")
    assert matched is True


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
