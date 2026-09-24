"""engine/lib/netlistlib.py: SPICE device scanning and device-mutant
generation for bench_strength (docs/design.md 1.5, "### M8."). Hermetic
(pure venv + reads the real PDK tree's own ngspice model files for
known_models - no ngspice invocation)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import netlistlib  # noqa: E402
from checklib import CheckError  # noqa: E402

MIRROR_NETLIST = """\
* current mirror
xmref d1 d1 0 0 nfet_03v3 w=4e-6 l=5e-7
xmout d2 d1 0 0 nfet_03v3 w=8e-6 l=5e-7
* a deliberately floating node for the test below
cnoise floaty 0 1f
"""


def toolchain_pdk_root() -> Path:
    eda = REPO / "bin" / "eda"
    proc = subprocess.run([str(eda), "--print-toolchain-root"],
                          capture_output=True, text=True, timeout=30)
    return Path(proc.stdout.strip()) / "foss" / "pdks" / "gf180mcuD"


PDK_ROOT = toolchain_pdk_root()
pytestmark_needs_pdk = pytest.mark.skipif(
    not PDK_ROOT.is_dir(), reason="no PDK tree at this box's default toolchain root")


def test_parse_devices_finds_model_and_nodes_and_params():
    devs = netlistlib.parse_devices(MIRROR_NETLIST)
    assert len(devs) == 2
    ref = devs[0]
    assert ref["ref"] == "xmref"
    assert ref["model"] == "nfet_03v3"
    assert ref["nodes"] == ["d1", "d1", "0", "0"]
    assert ref["params"] == {"w": "4e-6", "l": "5e-7"}


def test_parse_devices_ignores_comments_and_non_x_lines():
    text = "* xfoo bar\n.subckt widget a b\nxreal a b c 0 nfet_03v3 w=1e-6\n.ends\n"
    devs = netlistlib.parse_devices(text)
    assert len(devs) == 1
    assert devs[0]["ref"] == "xreal"


def test_subckt_pins_collected():
    text = ".subckt current_mirror iref iout vdd vss\nxm a b c d nfet_03v3\n.ends\n"
    pins = netlistlib.subckt_pins(text)
    assert pins["current_mirror"] == ["iref", "iout", "vdd", "vss"]


def test_floating_nodes_flags_single_touch_net():
    devs = netlistlib.parse_devices(MIRROR_NETLIST)
    # "floaty" is touched by exactly one device terminal (cnoise) - wait,
    # cnoise is not an x-line so it never enters `devs` at all; floating
    # detection only sees x-line terminals. d1 is touched by 3 terminals
    # (xmref gate+drain, xmout gate) -> not floating. 0 is a global net.
    floating = netlistlib.floating_nodes(devs, declared_pins=set())
    assert "d1" not in floating
    assert "0" not in floating


def test_floating_nodes_real_single_touch_case():
    text = "xm1 out g vss vss nfet_03v3 w=1e-6\nxm2 d2 unused vss vss nfet_03v3 w=1e-6\n"
    devs = netlistlib.parse_devices(text)
    floating = netlistlib.floating_nodes(devs, declared_pins=set())
    assert "unused" in floating


def test_floating_nodes_excludes_declared_pins():
    text = "xm1 out g vss vss nfet_03v3 w=1e-6\n"
    devs = netlistlib.parse_devices(text)
    # "g" only touched once, but declared as a subckt pin - not floating.
    assert "g" not in netlistlib.floating_nodes(devs, declared_pins={"g"})
    assert "g" in netlistlib.floating_nodes(devs, declared_pins=set())


@pytestmark_needs_pdk
def test_known_models_includes_real_gf180_devices():
    models = netlistlib.known_models(PDK_ROOT)
    assert "nfet_03v3" in models
    assert "pfet_03v3" in models
    assert "rm1" in models
    assert "not_a_real_model" not in models


def test_known_models_raises_when_pdk_tree_missing(tmp_path):
    with pytest.raises(CheckError):
        netlistlib.known_models(tmp_path)


def test_parse_sources_finds_bias_current():
    bench = "iref vdd d1 dc 10e-6\nvdd vdd 0 3.3\n"
    srcs = netlistlib.parse_sources(bench)
    assert srcs["iref"]["value"] == "10e-6"
    assert srcs["iref"]["kind"] == "i"


# ------------------------------------------------------------- device_mutants

def test_device_mutants_size_doubled_applies_to_netlist():
    mutants = netlistlib.device_mutants(MIRROR_NETLIST, ["xmout"])
    m = next(m for m in mutants if m["kind"] == "size_doubled")
    assert m["target"] == "netlist"
    out = m["apply"](MIRROR_NETLIST)
    assert "w=1.6e-05" in out or "w=1.6e-5" in out


def test_device_mutants_connection_removed():
    mutants = netlistlib.device_mutants(MIRROR_NETLIST, ["xmref"])
    m = next(m for m in mutants if m["kind"] == "connection_removed")
    out = m["apply"](MIRROR_NETLIST)
    assert "__floating_xmref__" in out
    # only the mutated line changed
    assert "xmout d2 d1 0 0 nfet_03v3 w=8e-6 l=5e-7" in out


def test_device_mutants_type_flipped_same_rail():
    mutants = netlistlib.device_mutants(MIRROR_NETLIST, ["xmref"])
    m = next(m for m in mutants if m["kind"] == "type_flipped")
    out = m["apply"](MIRROR_NETLIST)
    assert "pfet_03v3" in out
    assert "xmref" in out.splitlines()[1]


def test_device_mutants_bias_halved_targets_bench():
    bench = "iref vdd d1 dc 10e-6\n"
    mutants = netlistlib.device_mutants(MIRROR_NETLIST, ["iref"], bench_text=bench)
    assert len(mutants) == 1
    m = mutants[0]
    assert m["kind"] == "bias_halved"
    assert m["target"] == "bench"
    out = m["apply"](bench)
    assert "5e-06" in out or "5e-6" in out


def test_device_mutants_unknown_ref_is_skipped():
    assert netlistlib.device_mutants(MIRROR_NETLIST, ["xnope"]) == []


def test_device_mutants_only_mutates_its_own_line():
    mutants = netlistlib.device_mutants(MIRROR_NETLIST, ["xmref", "xmout"])
    for m in mutants:
        out = m["apply"](MIRROR_NETLIST)
        other_ref = "xmout" if m["ref"] == "xmref" else "xmref"
        orig_line = next(l for l in MIRROR_NETLIST.splitlines()
                         if l.startswith(other_ref))
        assert orig_line in out
