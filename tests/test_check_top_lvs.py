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


def test_macro_devices_counts_only_the_macro():
    layout = (".subckt inv A Y VDD VSS\nX0 Y A VDD VDD pfet_05v0 w=1u l=1u\n"
              ".ends\n" + MACRO)
    assert check_top_lvs.macro_devices(layout, "ana") == {"fet": 2}


def test_macro_devices_walks_subcells():
    layout = (".subckt half a b vdd\nX0 a b vdd vdd pfet_03v3 w=2u l=10u\n"
              ".ends\n.subckt ana a b vdd vss\nX1 a b vdd half\n"
              "X2 a b vss vss nfet_03v3 w=1u l=10u\n.ends\n")
    assert check_top_lvs.macro_devices(layout, "ana") == {"fet": 2}


def test_blackboxed_macro_counts_zero():
    assert not check_top_lvs.macro_devices(".subckt ana a b vdd vss\n.ends\n",
                                           "ana")
    assert not check_top_lvs.macro_devices("", "ana")


def test_device_kinds_cover_the_pdk_primitives():
    kinds = {m: check_top_lvs.device_kind(m) for m in (
        "nfet_03v3", "pfet_05v0", "ppolyf_u", "npolyf_s", "ppolyf_u_1k",
        "rm1", "nplus_u", "cap_mim_2f0fF", "cap_nmos_03v3",
        "diode_nd2ps_06v0", "npn_10p00x10p00", "pnp_05p00x00p42",
        "gf180mcu_fd_sc_mcu7t5v0__buf_20", "1k")}
    assert kinds == {
        "nfet_03v3": "fet", "pfet_05v0": "fet", "ppolyf_u": "res",
        "npolyf_s": "res", "ppolyf_u_1k": "res", "rm1": "res",
        "nplus_u": "res", "cap_mim_2f0fF": "cap", "cap_nmos_03v3": "cap",
        "diode_nd2ps_06v0": "diode", "npn_10p00x10p00": "bjt",
        "pnp_05p00x00p42": "bjt", "gf180mcu_fd_sc_mcu7t5v0__buf_20": None,
        "1k": None}


# a stand-in for the standard-cell spice: the R-2R DAC's buf_20 drivers
STDLIB = """.SUBCKT gf180mcu_fd_sc_mcu7t5v0__buf_20 I Z VDD VNW VPW VSS
X_i_0 Z_neg I VSS VPW nfet_05v0 W=8.2e-07 L=6e-07
X_i_1 Z_neg I VDD VNW pfet_05v0 W=1.2e-06 L=5e-07
X_i_2 Z Z_neg VSS VPW nfet_05v0 W=8.2e-07 L=6e-07
X_i_3 Z Z_neg VDD VNW pfet_05v0 W=1.2e-06 L=5e-07
.ENDS
"""
# the R-2R macro: two std-cell drivers and four poly resistors, no FET of
# its own - what the FET-only guard read as an empty macro
R2R = """.subckt r2r b0 b1 vout vdd vss
xd0 b0 d0 vdd vdd vss vss gf180mcu_fd_sc_mcu7t5v0__buf_20
xd1 b1 d1 vdd vdd vss vss gf180mcu_fd_sc_mcu7t5v0__buf_20
xr0 d0 n0 vss ppolyf_u r_width=1e-6 r_length=1e-5
xr1 n0 vss vss ppolyf_u r_width=1e-6 r_length=1e-5 m=2
xr2 d1 vout vss ppolyf_u
+ r_width=1e-6 r_length=1e-5
.ends r2r
"""
# what magic writes for it: the std cell extracted as its own subckt, the
# resistors as rsubcircuit devices, instance names its own
R2R_LAYOUT = """.subckt gf180mcu_fd_sc_mcu7t5v0__buf_20 I Z VDD VSS VNW VPW
X0 Z_neg I VSS VPW nfet_05v0 ad=1p pd=1u as=1p ps=1u w=0.82u l=0.6u
X1 Z_neg I VDD VNW pfet_05v0 ad=1p pd=1u as=1p ps=1u w=1.2u l=0.5u
X2 Z Z_neg VSS VPW nfet_05v0 ad=1p pd=1u as=1p ps=1u w=0.82u l=0.6u
X3 Z Z_neg VDD VNW pfet_05v0 ad=1p pd=1u as=1p ps=1u w=1.2u l=0.5u
.ends
.subckt r2r b0 b1 vout vdd vss
X0 b0 d0 vdd vss vdd vss gf180mcu_fd_sc_mcu7t5v0__buf_20
X1 b1 d1 vdd vss vdd vss gf180mcu_fd_sc_mcu7t5v0__buf_20
X2 d0 n0 vss ppolyf_u r_width=1u r_length=10u
X3 n0 vss vss ppolyf_u r_width=1u r_length=10u
X4 n0 vss vss ppolyf_u r_width=1u r_length=10u
X5 d1 vout vss ppolyf_u r_width=1u r_length=10u
.ends
"""


def lib():
    return check_top_lvs.subckt_bodies(STDLIB)


def test_std_cells_and_resistors_macro_passes():
    got, want = check_top_lvs.macro_guard([R2R], R2R_LAYOUT, lib())
    assert want == {"fet": 8, "res": 4} and got == want


def test_std_cells_left_abstract_in_the_extraction_expand_from_the_library():
    abstract = R2R_LAYOUT[R2R_LAYOUT.index(".subckt r2r"):]
    got, want = check_top_lvs.macro_guard([R2R], abstract, lib())
    assert got == want == {"fet": 8, "res": 4}


def test_blackboxed_std_cell_macro_refuses():
    for layout in ("", ".subckt r2r b0 b1 vout vdd vss\n.ends\n",
                   # the macro as one unexpanded instance of itself
                   ".subckt top a\nX0 a b c d e r2r\n.ends\n"):
        with pytest.raises(CheckError, match="blackbox"):
            check_top_lvs.macro_guard([R2R], layout, lib())


def test_missing_resistors_refuse_even_with_the_fets_there():
    no_res = "\n".join(l for l in R2R_LAYOUT.splitlines()
                       if "ppolyf" not in l) + "\n"
    with pytest.raises(CheckError, match="blackbox"):
        check_top_lvs.macro_guard([R2R], no_res, lib())


def test_empty_macro_subckt_refuses():
    with pytest.raises(CheckError, match="empty"):
        check_top_lvs.macro_guard([".subckt r2r a b\n.ends\n"], R2R_LAYOUT,
                                  lib())


def test_std_cells_unknown_to_the_library_count_nothing():
    drivers = "\n".join(l for l in R2R.splitlines()
                        if "ppolyf" not in l and not l.startswith("+")) + "\n"
    assert check_top_lvs.macro_guard([drivers], R2R_LAYOUT, lib())[1] == {
        "fet": 8}
    with pytest.raises(CheckError, match="blackbox"):
        check_top_lvs.macro_guard([drivers], R2R_LAYOUT, {})


def test_fet_only_macro_behaves_as_before():
    layout = ".subckt ana a b vdd vss\nX0 a b vdd vdd pfet_03v3 w=2u l=10u\n" \
             "X1 a b vss vss nfet_03v3 w=1u l=10u\n.ends\n"
    assert check_top_lvs.macro_guard([MACRO], layout, lib()) == (
        {"fet": 2}, {"fet": 2})
    with pytest.raises(CheckError, match="blackbox"):
        check_top_lvs.macro_guard([MACRO], layout.replace(
            "X1 a b vss vss nfet_03v3 w=1u l=10u\n", ""), lib())
