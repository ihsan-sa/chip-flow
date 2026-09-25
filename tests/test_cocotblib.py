"""engine/lib/cocotblib.py: shared cocotb/Icarus plumbing (sim, holdout,
mutate, cover, cosim - docs/design.md "### M2."). Only run_cocotb's own
wall-clock timeout is exercised directly here (a real build+test needs the
eda image and belongs to each gate's own slow tests instead) - a
monkeypatched `cocotb_tools.runner.get_runner` stands in for cocotb so this
stays fast."""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import cocotblib  # noqa: E402


class _HangingRunner:
    """Stands in for cocotb_tools.runner's Icarus runner: build() never
    returns on its own within the timeout, matching a cosim bench wedged
    inside ngspice's shared library on a sync point that never comes (no
    subprocess, no exit code - CocotbTimeout is the only way out)."""

    def build(self, **kwargs):
        time.sleep(5)

    def test(self, **kwargs):
        raise AssertionError("test() must never run - build() should have "
                             "timed out first")


def test_run_cocotb_raises_on_wall_clock_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cocotb_tools.runner.get_runner", lambda name: _HangingRunner())

    build_dir = tmp_path / "build"
    started = time.monotonic()
    with pytest.raises(cocotblib.CocotbTimeout):
        cocotblib.run_cocotb(
            build_dir, tmp_path, [tmp_path / "top.v"], "top", ["test_top"],
            tmp_path / "results.xml", timeout_s=1.0)
    elapsed = time.monotonic() - started
    assert elapsed < 5, (
        f"took {elapsed:.1f}s - the 1s timeout did not actually fire")


def test_run_cocotb_without_timeout_is_unaffected(tmp_path, monkeypatch):
    # timeout_s=None (the default) must change nothing for every existing
    # caller (check_sim.py, check_holdout.py, check_cover.py,
    # mutate_runner.py) - no SIGALRM ever armed.
    calls = []

    class _FastRunner:
        def build(self, **kwargs):
            calls.append("build")

        def test(self, **kwargs):
            calls.append("test")
            return tmp_path / "results.xml"

    monkeypatch.setattr(
        "cocotb_tools.runner.get_runner", lambda name: _FastRunner())
    result = cocotblib.run_cocotb(
        tmp_path / "build", tmp_path, [tmp_path / "top.v"], "top",
        ["test_top"], tmp_path / "results.xml")
    assert calls == ["build", "test"]
    assert result == tmp_path / "results.xml"


def test_stacked_req_lines_all_tag_the_test(tmp_path):
    """ece298a round 2, breakage 11: two `# req:` lines stacked above one
    test used to keep only the last. Both count now; a code line between a
    tag and the next test still drops the tag."""
    (tmp_path / "test_x.py").write_text(
        "import cocotb\n\n"
        "# req: R1\n"
        "# covers the reset path too\n"
        "# req: R2 R3\n"
        "@cocotb.test()\n"
        "async def test_stacked(dut):\n"
        "    pass\n\n"
        "# req: R9\n"
        "X = 1\n"
        "# req: R4\n"
        "@cocotb.test()\n"
        "async def test_single(dut):\n"
        "    pass\n",
        encoding="utf-8")
    tags = cocotblib.scan_requirement_tags(tmp_path)
    assert tags == {"test_stacked": {"R1", "R2", "R3"},
                    "test_single": {"R4"}}
