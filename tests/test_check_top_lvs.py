"""check_top_lvs.judge - reading netgen's verdict, without running netgen."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1] / "engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))

import check_top_lvs  # noqa: E402
from checklib import CheckError  # noqa: E402

CLEAN = ("Netlists match uniquely.\n"
         "Device classes top and top are equivalent.\n\n"
         "Final result: Circuits match uniquely.\n")


def test_clean_match_passes():
    assert check_top_lvs.judge(CLEAN, "") == ("Circuits match uniquely.", True)


def test_property_error_fails():
    rpt = ("Netlists match uniquely with property errors.\n"
           "Property w in circuit1 is 1u, in circuit2 is 2u\n" + CLEAN)
    assert check_top_lvs.judge(rpt, "")[1] is False


def test_unmatched_subcell_fails():
    rpt = "Netlists do not match.\n" + CLEAN
    assert check_top_lvs.judge(rpt, "")[1] is False


def test_pin_mismatch_fails():
    final, matched = check_top_lvs.judge(
        "Final result: Top level cell failed pin matching.\n", "")
    assert final == "Top level cell failed pin matching." and not matched


def test_no_verdict_refuses():
    with pytest.raises(CheckError):
        check_top_lvs.judge("", "segfault")


def test_setup_file_errors_refuse():
    out = ("Error setup.tcl:1 (ignoring), can't read \"::env(NETGEN_SETUP)\"\n"
           "Warning:  There were errors reading the setup file\n")
    with pytest.raises(CheckError, match="setup file"):
        check_top_lvs.judge(CLEAN, out)


MACRO = """.subckt ana a b vdd vss
xm1 a b vdd vdd pfet_03v3 w=2e-6 l=1e-5
xm2 a b vss vss nfet_03v3 w=1e-6 l=1e-5
.ends ana
"""


def test_macro_fets_counts_only_the_macro():
    layout = (".subckt inv A Y VDD VSS\nX0 Y A VDD VDD pfet_05v0 w=1u l=1u\n"
              ".ends\n" + MACRO)
    assert check_top_lvs.macro_fets(layout, "ana") == 2


def test_macro_fets_walks_subcells():
    layout = (".subckt half a b vdd\nX0 a b vdd vdd pfet_03v3 w=2u l=10u\n"
              ".ends\n.subckt ana a b vdd vss\nX1 a b vdd half\n"
              "X2 a b vss vss nfet_03v3 w=1u l=10u\n.ends\n")
    assert check_top_lvs.macro_fets(layout, "ana") == 2


def test_blackboxed_macro_counts_zero():
    assert check_top_lvs.macro_fets(".subckt ana a b vdd vss\n.ends\n",
                                    "ana") == 0
    assert check_top_lvs.macro_fets("", "ana") == 0
