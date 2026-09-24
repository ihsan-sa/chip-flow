"""Fault: the RTL changed after every gate (sim and the rest) already
recorded a pass - the release row's own named fault (gates.yaml: "an RTL
edit after harden, which must refuse"). Fabricates a fully-passed pipeline
first (record_gate hashes the CURRENT files itself at record time - the
same trick tests/test_check_release.py's pass_every_gate uses) so the
refusal this proves is specifically about staleness, never about a gate
that simply never ran. `sim` itself is not re-run afterward - "release
refuses a workspace whose RTL changed after sim passed" (docs/design.md
"### M3.") means sim's own recorded result must go stale, not that this
plant script re-runs anything."""
from pathlib import Path

import state as state_mod
import statelib

# A trailing comment is legal after `endmodule` and changes the file's own
# dir_text hash without touching the design's behavior - the RTL "changing"
# is the whole point here, not what it changes to.
EDIT_MARKER = "\n// RTL changed after the recorded pass\n"


def plant(ws: Path) -> None:
    st = state_mod.State.load(ws / "state.json")
    for gate in statelib.load_map()["gate_inputs"]["vde"]:
        st.record_gate(gate, {"status": "pass"})
    st.save()
    for f in sorted((ws / "rtl").glob("*.v")):
        f.write_text(f.read_text(encoding="utf-8") + EDIT_MARKER,
                     encoding="utf-8")
