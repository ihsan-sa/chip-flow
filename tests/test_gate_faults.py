"""Every gate in engine/reference/gates.yaml has a planted fault in some
corpus rung's faults/manifest.yaml for its skill, and every manifest entry
names a real gate of that skill (docs/design.md 1.5's third column: the
fault each gate must catch). Reads YAML only, so it runs in tests/check.sh.

UNPLANTED lists the gates that have no planted fault yet, each with why. A
gate drops off it when a rung plants its fault; the GOALS.md goal "Plant a
fault for every gate" is done when it is empty."""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
GATES = REPO / "engine" / "reference" / "gates.yaml"
CORPUS = REPO / "corpus"

UNPLANTED: dict[tuple[str, str], str] = {}


def _gates() -> dict[str, set[str]]:
    data = yaml.safe_load(GATES.read_text(encoding="utf-8"))
    return {skill: set(rows) for skill, rows in data["gates"].items()}


def _planted() -> dict[str, dict[str, list[str]]]:
    """skill -> gate -> the manifest entries (rung/name) that plant it."""
    out: dict[str, dict[str, list[str]]] = {}
    for manifest in sorted(CORPUS.glob("*/*/faults/manifest.yaml")):
        skill, rung = manifest.parts[-4], manifest.parts[-3]
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        for fault in data["faults"]:
            out.setdefault(skill, {}).setdefault(fault["gate"], []).append(
                f"{rung}/{fault['name']}")
    return out


def test_corpus_has_manifests_for_every_skill():
    assert set(_planted()) == set(_gates()), \
        "a skill in gates.yaml has no corpus manifest - did the corpus move?"


def test_every_manifest_gate_is_a_real_gate_of_its_skill():
    gates = _gates()
    bad = [f"{skill}: {gate} ({', '.join(where)})"
           for skill, by_gate in _planted().items()
           for gate, where in sorted(by_gate.items())
           if gate not in gates.get(skill, set())]
    assert not bad, f"manifest entries name no gates.yaml gate: {bad}"


def test_every_gate_has_a_planted_fault():
    planted = _planted()
    missing = sorted((skill, gate) for skill, rows in _gates().items()
                     for gate in rows
                     if gate not in planted.get(skill, {})
                     and (skill, gate) not in UNPLANTED)
    assert not missing, \
        f"gates with no planted fault in any corpus manifest: {missing}"


def test_unplanted_lists_only_gates_still_unplanted():
    """An UNPLANTED entry whose gate now has a fault (or no longer exists)
    is stale: drop it, so the list stays the goal's honest to-do."""
    planted, gates = _planted(), _gates()
    stale = sorted(key for key in UNPLANTED
                   if key[1] in planted.get(key[0], {})
                   or key[1] not in gates.get(key[0], set()))
    assert not stale, f"UNPLANTED entries to drop: {stale}"
