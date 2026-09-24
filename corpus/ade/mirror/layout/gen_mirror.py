"""gen_mirror.py - layout generator for the ade/mirror corpus rung
(docs/design.md 5, "### M9."; docs/spikes/glayout.md's fallback).

A 2-NMOS current mirror: M1 diode-connected (drain tied to gate), M2's gate
tied to M1's, both sources to VSS. Both transistors are GF180's own
draw_nfet() at its declared defaults (l_gate=0.28um, w_gate=0.22um, nf=1) -
verified against the real klayout GF180 signoff deck to be one violation
(DF.14, "substrate tap must be within 20um of any NCOMP") away from clean
in isolation; layoutlib.psub_tap() supplies that tap, tied to the same VSS
net the two sources use.

All coordinates below are the DEFAULT draw_nfet() cell's own pad centers,
measured once (see docs/design.md 5's spike) and reused as constants -
changing l_gate/w_gate/nf from these exact values would move the pads and
invalidate the routing geometry, so this generator does not parametrize
them. Local pad boxes (x0, y0, x1, y1), un-transformed:
    gate (top)    (0.14, 0.51, 0.52, 0.89)   - used as the gate connection
    gate (bottom) (0.14, -0.67, 0.52, -0.29) - unused; still live (same net)
    source        (-0.40, -0.08, -0.02, 0.30)
    drain         (0.68, -0.08, 1.06, 0.30)
    device bbox   (-0.59, -0.67, 1.25, 0.89)

Net layout (M1 at x-offset 0, M2 at x-offset MIRROR_DX):
  IREF/VG  gate bus across both tops, plus M1's own drain-to-gate diode drop
  IOUT     M2's drain pad (labeled in place, no extra routing)
  VSS      both sources dropped below the gate geometry to a bus that also
           reaches one shared psub_tap()

The "missing guard ring" DRC fault (faults/plant_drc_missing_guard_ring.py)
removes just the two psub_tap placement lines below, leaving the rest of
the VSS wiring (both source drops and the bus) intact - a real missing
guard ring, not a disconnected VSS net.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "engine" / "lib"))
import layoutlib  # noqa: E402

L1 = layoutlib.GF180_LAYER["metal1"]
L1LBL = layoutlib.GF180_LAYER["metal1_label"]

MIRROR_DX = 3.0
TAP_X = 6.75
TAP_Y = -1.6
TAP_SIZE = 1.0


def generate():
    import gdsfactory as gf

    draw_fet, _draw_res = layoutlib.gf180_cells()
    top = gf.Component()

    m1 = top.add_ref(draw_fet.draw_nfet())
    m2 = top.add_ref(draw_fet.draw_nfet())
    m2.move((MIRROR_DX, 0.0))

    labels = []

    # --- IREF/VG: gate bus (both tops) + M1's own drain-to-gate diode drop
    layoutlib.rect(top, 0.14, 0.51, MIRROR_DX + 0.52, 0.89, L1)   # gate bus
    layoutlib.rect(top, 0.14, 0.0, 0.52, 0.55, L1)                # vertical drop
    layoutlib.rect(top, 0.14, 0.0, 1.06, 0.30, L1)                # to M1 drain
    labels.append(("IREF", 1.5, 0.7, L1LBL))

    # --- IOUT: M2's own drain pad, no extra routing needed
    labels.append(("IOUT", MIRROR_DX + 0.87, 0.11, L1LBL))

    # --- VSS: source drops, the bus, and the shared substrate tap.
    # plant_drc_missing_guard_ring.py deletes only the two `tap = ...` /
    # `tap.move(...)` lines just below, leaving the rest of this section
    # (both source drops, the bus) intact.
    layoutlib.rect(top, -0.40, -0.95, -0.02, 0.0, L1)             # M1 source drop
    layoutlib.rect(top, MIRROR_DX - 0.40, -0.95, MIRROR_DX - 0.02, 0.0, L1)  # M2
    tap = top.add_ref(layoutlib.psub_tap(TAP_SIZE))
    tap.move((TAP_X, TAP_Y))
    layoutlib.rect(top, -0.40, -1.2, TAP_X + 0.8, -0.95, L1)      # VSS bus + tap
    labels.append(("VSS", 2.0, -1.075, L1LBL))

    return layoutlib.finalize(top, "mirror", labels)


if __name__ == "__main__":
    comp = generate()
    out = sys.argv[1] if len(sys.argv) > 1 else "mirror.gds"
    comp.write_gds(out)
    print(f"wrote {out}")
