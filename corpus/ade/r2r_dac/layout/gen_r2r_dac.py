"""gen_r2r_dac.py - layout generator for the ade/r2r_dac corpus rung
(docs/design.md 5, "### M9."; docs/spikes/glayout.md's fallback).

The N=1 base case of an R-2R ladder: two matched "2R" poly resistors
(GF180's own draw_npolyf_res()) forming a 2:1 divider, VOUT = VIN/2
unloaded - R_A between VIN and VOUT, R_B between VOUT and GND. Extending to
more bits is the same R_B-continues-into-R_A-of-the-next-stage pattern
repeated; kept to one stage here so the hand-routing stays small enough to
verify by hand against the real signoff deck in one sitting (M9's own
boundary: prove the fallback route works before building the skill on it).

Both resistors are draw_npolyf_res(l_res=4.0, w_res=0.6) - w_res>=0.564 is
required for the vendor generator's own built-in substrate tap to clear
DF.9 (min COMP area) after the 5nm grid-snap it applies internally (below
that, snap-to-nearest can round the tap's already-marginal area below the
DRC minimum - verified empirically, docs/spikes/glayout.md's own class of
finding). Local pad boxes for these exact parameters, un-transformed:
    sub tap   (-1.38, 0.11, -1.00, 0.49)
    R0 (left) (-0.30, 0.11,  0.08, 0.49)
    R1 (right) (3.92, 0.11,  4.30, 0.49)
    bbox      (-1.53, -0.30, 4.59, 0.90)

Both instances' substrate taps tie into the same GND bus as R_B's own GND
terminal (a floating poly-resistor tap is not connected to anything a real
LVS schematic would name, and GF180's own rules treat it as a P+
tap-to-substrate tie, so grounding it is the correct, not just convenient,
choice).

VOUT (R_A.R1 to R_B.R0) cannot be a straight metal1 run: R_B's own
substrate-tap pad sits at local x:(-1.38,-1.0), which lands INSIDE that
run's x-span once R_B is placed - a first version routed it in metal1 and
magic's extraction reported "Ports GND and VOUT are electrically shorted"
(the GND drop to that tap crossed straight through the VOUT wire on the
same layer). Fixed by jumping VOUT over the tap on metal2 (GF180's own
via_stack(), also from the pymacros tree) instead of moving the tap, which
is standard practice, not a workaround.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "engine" / "lib"))
import layoutlib  # noqa: E402

L1 = layoutlib.GF180_LAYER["metal1"]
L1LBL = layoutlib.GF180_LAYER["metal1_label"]

RES_L = 4.0
RES_W = 0.6
DAC_DX = 8.0


def generate():
    import gdsfactory as gf

    _draw_fet, draw_res = layoutlib.gf180_cells()
    top = gf.Component()

    ra = top.add_ref(draw_res.draw_npolyf_res(l_res=RES_L, w_res=RES_W))
    rb = top.add_ref(draw_res.draw_npolyf_res(l_res=RES_L, w_res=RES_W))
    rb.move((DAC_DX, 0.0))

    labels = []

    # VIN: R_A's own left (R0) pad, labeled in place
    labels.append(("VIN", -0.11, 0.30, L1LBL))

    # VOUT: R_A.R1 -- R_B.R0, jumped onto metal2 (see module docstring: a
    # metal1 run here would cross straight through R_B's own substrate tap)
    from cells.via_generator import via_stack  # noqa: E402  (gf180_cells() put it on sys.path)
    top.add_ref(via_stack(x_range=(3.92, 4.30), y_range=(0.11, 0.49),
                          metal_level=2, base_layer=L1))
    top.add_ref(via_stack(x_range=(DAC_DX - 0.30, DAC_DX + 0.08),
                          y_range=(0.11, 0.49), metal_level=2, base_layer=L1))
    layoutlib.rect(top, 3.92, 0.11, DAC_DX + 0.08, 0.49,
                  layoutlib.GF180_LAYER["metal2"])
    # VOUT's own wire at this x,y is metal2, not metal1 - a metal1_label
    # here would name an empty spot (this cost one broken LVS net: magic
    # invented a floating "VOUT" port instead of tagging R_A/R_B's shared
    # node, because no metal1_label may claim metal2 geometry).
    labels.append(("VOUT", 6.0, 0.30, layoutlib.GF180_LAYER["metal2_label"]))

    # GND: R_B.R1 + both substrate taps
    layoutlib.rect(top, DAC_DX + 3.92, -0.9, DAC_DX + 4.30, 0.15, L1)  # R_B.R1 drop
    layoutlib.rect(top, -1.38, -0.9, -1.00, 0.15, L1)                 # SUB_A drop
    layoutlib.rect(top, DAC_DX - 1.38, -0.9, DAC_DX - 1.00, 0.15, L1)  # SUB_B drop
    layoutlib.rect(top, -1.38, -0.9, DAC_DX + 4.30, -0.6, L1)         # GND bus
    labels.append(("GND", 2.0, -0.75, L1LBL))

    return layoutlib.finalize(top, "r2r_dac", labels)


if __name__ == "__main__":
    comp = generate()
    out = sys.argv[1] if len(sys.argv) > 1 else "r2r_dac.gds"
    comp.write_gds(out)
    print(f"wrote {out}")
