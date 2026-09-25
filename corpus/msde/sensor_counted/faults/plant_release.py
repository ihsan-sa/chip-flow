"""Fault: a stale nested release (gates.yaml's msde release row). Records a
pass for every gate of both nested sides and writes each side's own
release record, records a pass for every msde gate, then edits a file in
the DIGITAL side's tb/ - an input of that side's own release but of no msde
gate - so the only thing wrong is that digital/'s release no longer
verifies. Nothing is re-run: the recorded results are the point."""
from pathlib import Path

import attest as attest_mod
import state as state_mod
import statelib


def _pass_all(ws: Path, skill: str) -> None:
    st = state_mod.State.load(ws / "state.json")
    for gate in statelib.load_map()["gate_inputs"][skill]:
        st.record_gate(gate, {"status": "pass"})
    st.save()


def plant(ws: Path) -> None:
    for side, skill in (("digital", "vde"), ("analog", "ade")):
        _pass_all(ws / side, skill)
        att, problems = attest_mod.build(ws / side)
        if att is None:
            raise RuntimeError(f"plant_release.py: {side}/ would not "
                               f"release: {problems}")
        attest_mod.write_attestation(ws / side, att)
    _pass_all(ws, "msde")
    tb = sorted(p for p in (ws / "digital" / "tb").iterdir() if p.is_file())
    tb[0].write_text(tb[0].read_text(encoding="utf-8")
                     + "\n# edited after the digital side released\n",
                     encoding="utf-8")
