"""Every ade and msde corpus rung's planted faults, run end to end through
faults.py's own run_rung: the untouched reference passes every gate its
faults/manifest.yaml names, and each planted fault is caught with the
expected finding (docs/design.md section 3). tests/smoke-faults.sh does
this by hand for vde only; these are marked slow, so tests/check-slow.sh
(and its CI job) runs them, not tests/check.sh. Measured unmarked on
2026-10-15: the seven rungs run here took about 45 s of CPU together
(comparator 25 s, mirror 11 s, bandgap 7 s, the rest under 4 s each).

HELD_BACK lists the rungs not run here yet, each with why. A rung drops
off it when its run fits the slow lane; the GOALS.md goal "Run every ade
and msde rung's planted faults on CI" is done when it is empty."""
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

HELD_BACK: dict[tuple[str, str], str] = {
    ("msde", "dac_tile"): "top_harden, top_lvs and precheck each need a full "
                          "LibreLane top harden",
    ("msde", "sensor_counted"): "top_harden, top_drc and top_lvs each need a "
                                "full LibreLane top harden",
}


def _rungs() -> list[tuple[str, str]]:
    return [(m.parts[-4], m.parts[-3]) for skill in SKILLS
            for m in sorted((CORPUS / skill).glob("*/faults/manifest.yaml"))]


def test_held_back_names_only_real_rungs():
    stale = sorted(set(HELD_BACK) - set(_rungs()))
    assert not stale, f"HELD_BACK entries with no corpus manifest: {stale}"


@pytest.mark.slow
@pytest.mark.parametrize("skill, rung", [r for r in _rungs()
                                         if r not in HELD_BACK])
def test_rung_catches_every_planted_fault(tmp_path, skill, rung):
    import faults
    import gate as gate_mod

    gates = gate_mod.load_gates(gate_mod.DEFAULT_GATES)
    result = faults.run_rung(tmp_path, CORPUS / skill / rung, skill, gates)
    assert not result["problems"], result
