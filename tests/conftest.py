"""Shared pytest config for the engine suite.

Registers the `slow` marker: a real-tool test (needs the eda image - sby,
yosys, verilator, mcy, cocotb) rather than a Python-side unit test.
CLAUDE.md keeps tests/check.sh under two minutes; tests/check-engine.sh
selects `-m "not slow"` to hold that budget, and tests/check-slow.sh runs
the `slow` set separately under its own, longer hang cap. Registered here
(rather than left implicit) so an unmarked use of `pytest.mark.slow` never
warns as unknown, and `pytest --markers` documents it."""
from __future__ import annotations


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "slow: a real-tool test that needs the eda image - excluded from "
        "tests/check-engine.sh's own run, covered by tests/check-slow.sh",
    )


import pytest  # noqa: E402


@pytest.fixture
def unlaunchable_eda(tmp_path, monkeypatch):
    """The three ade layout gates, wired to an `eda` launcher that does not
    exist: a fake PDK tree holding the deck, magic tech and netgen setup
    each gate checks for, a stub generator build, and layoutlib.EDA_BIN
    pointing at nothing. Returns the workspace. A gate run on it must come
    back red (exit 2), never pass on whatever an earlier run left in log/."""
    import sys
    from pathlib import Path

    engine = Path(__file__).resolve().parents[1] / "engine"
    for p in (engine / "scripts", engine / "lib"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import layout_gen
    import layoutlib

    pdk = tmp_path / "pdk" / "gf180mcuD"
    deck = pdk / layoutlib.DRC_DECK_REL
    deck.parent.mkdir(parents=True)
    deck.write_text("# fake deck\n", encoding="utf-8")
    tech = pdk / "libs.tech" / "magic" / "gf180mcuD.tech"
    tech.parent.mkdir(parents=True)
    tech.write_text(" calma MET1RES 110 11\n", encoding="utf-8")
    setup = pdk / "libs.tech" / "netgen" / "gf180mcuD_setup.tcl"
    setup.parent.mkdir(parents=True)
    setup.write_text("# fake setup\n", encoding="utf-8")
    monkeypatch.setattr(layoutlib, "pdk_root", lambda: pdk)
    monkeypatch.setattr(layoutlib, "EDA_BIN", tmp_path / "no-such-eda")

    ws = tmp_path / "ws"
    for d in ("layout", "netlist", "layout_ref", "log/lvs", "log/pex_sim"):
        (ws / d).mkdir(parents=True)
    (ws / "state.json").write_text('{"block": "blk"}', encoding="utf-8")
    (ws / "netlist" / "blk.cir").write_text(
        ".subckt blk a b\nr1 a b 1k\n.ends\n", encoding="utf-8")
    (ws / "layout_ref" / "blk_pex_tb.cir").write_text(
        "* {pdk} {extracted}\n", encoding="utf-8")
    (ws / "layout_ref" / "blk_pex_tb.bounds.json").write_text(
        '{"measures": {"x": {"min": 0, "max": 1}}}', encoding="utf-8")
    # what a passing earlier run would have left behind in log/
    (ws / "log" / "analog_drc.lyrdb").write_text(
        "<report-database><items/></report-database>", encoding="utf-8")
    (ws / "log" / "lvs" / "netgen_lvs.log").write_text(
        "Final result: Circuits match uniquely.\n", encoding="utf-8")
    (ws / "log" / "lvs" / "blk.spice").write_text(
        ".subckt blk a b\nr1 a b 1k\n.ends\n", encoding="utf-8")

    gds = ws / "layout" / "blk.gds"
    gds.write_bytes(b"")
    monkeypatch.setattr(layout_gen, "build",
                        lambda ws_, block=None: (gds, "blk", None, {}))
    return ws
