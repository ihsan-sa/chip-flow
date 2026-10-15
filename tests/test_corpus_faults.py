"""Every ade and msde corpus rung's planted faults, run end to end through
faults.py's own run_rung: the untouched reference passes every gate its
faults/manifest.yaml names, and each planted fault is caught with the
expected finding (docs/design.md section 3). tests/smoke-faults.sh does
this by hand for vde only; these are marked slow, so tests/check-slow.sh
(and its CI job) runs them, not tests/check.sh. Measured unmarked on
2026-10-15: the seven rungs run here took about 45 s of CPU together
(comparator 25 s, mirror 11 s, bandgap 7 s, the rest under 4 s each).

HARDEN_RUNGS are the rungs whose run needs full LibreLane top hardens
(about three each: the clean reference, then each plant that hardens the
untouched top first). They are marked `harden_faults` as well as `slow`:
check-engine.sh's `not slow` keeps them out of check.sh, check-slow.sh's
`slow and not harden_faults` keeps them out of its 1800 s cap, and
tests/check-harden-faults.sh runs them, one CI job per rung.

HELD_BACK lists the rungs not run in either lane yet, each with why; the
GOALS.md goal "Run every ade and msde rung's planted faults on CI" is done
when it is empty and both lanes' CI jobs pass."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))
CORPUS = REPO / "corpus"
SKILLS = ("ade", "msde")

HARDEN_RUNGS: dict[tuple[str, str], str] = {
    ("msde", "dac_tile"): "top_harden, top_lvs and precheck each need a full "
                          "LibreLane top harden",
    ("msde", "sensor_counted"): "top_harden, top_drc and top_lvs each need a "
                                "full LibreLane top harden",
}
HELD_BACK: dict[tuple[str, str], str] = {}


def _rungs() -> list[tuple[str, str]]:
    return [(m.parts[-4], m.parts[-3]) for skill in SKILLS
            for m in sorted((CORPUS / skill).glob("*/faults/manifest.yaml"))]


def test_held_back_and_harden_rungs_name_only_real_rungs():
    for name, rungs in (("HELD_BACK", HELD_BACK),
                        ("HARDEN_RUNGS", HARDEN_RUNGS)):
        stale = sorted(set(rungs) - set(_rungs()))
        assert not stale, f"{name} entries with no corpus manifest: {stale}"
    both = sorted(set(HELD_BACK) & set(HARDEN_RUNGS))
    assert not both, f"held back and run in the harden lane: {both}"


def _run(tmp_path, skill, rung):
    import faults
    import gate as gate_mod

    gates = gate_mod.load_gates(gate_mod.DEFAULT_GATES)
    result = faults.run_rung(tmp_path, CORPUS / skill / rung, skill, gates)
    assert not result["problems"], result


@pytest.mark.slow
@pytest.mark.parametrize("skill, rung", [
    r for r in _rungs() if r not in HELD_BACK and r not in HARDEN_RUNGS])
def test_rung_catches_every_planted_fault(tmp_path, skill, rung):
    _run(tmp_path, skill, rung)


@pytest.mark.slow
@pytest.mark.harden_faults
@pytest.mark.parametrize("skill, rung", [
    r for r in _rungs() if r in HARDEN_RUNGS and r not in HELD_BACK])
def test_harden_rung_catches_every_planted_fault(tmp_path, skill, rung):
    _run(tmp_path, skill, rung)
