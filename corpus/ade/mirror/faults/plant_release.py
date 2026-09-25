"""Fault: the netlist changed after every ade gate already recorded a pass
- the /ade form of the release row's own fault ("as digital": vde's is an
RTL edit after harden). Fabricates a fully-passed pipeline first
(record_gate hashes the CURRENT files itself at record time, the same
trick corpus/vde/counter8's plant_release.py uses) so the refusal this
proves is about staleness, never about a gate that simply never ran. `mc`
is left unrecorded on purpose: this rung's spec.yaml asks for no Monte
Carlo, so release reads it as declared not applicable."""
from pathlib import Path

import state as state_mod
import statelib

# A trailing SPICE comment changes the netlist's dir_text hash without
# touching the circuit - the netlist "changing" is the whole point here.
EDIT_MARKER = "\n* netlist changed after the recorded pass\n"


def plant(ws: Path) -> None:
    st = state_mod.State.load(ws / "state.json")
    for gate in statelib.load_map()["gate_inputs"]["ade"]:
        if gate not in ("release", "mc"):
            st.record_gate(gate, {"status": "pass"})
    st.save()
    for f in sorted((ws / "netlist").glob("*.cir")):
        f.write_text(f.read_text(encoding="utf-8") + EDIT_MARKER,
                     encoding="utf-8")
