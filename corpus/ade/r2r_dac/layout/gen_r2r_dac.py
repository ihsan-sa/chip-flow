"""gen_r2r_dac.py - layout generator for the ade/r2r_dac corpus rung
(docs/design.md 5, "### M9."; docs/spikes/glayout.md's fallback).

Draws M8's own 2-bit ladder (netlist/r2r_dac.cir): four rm1 metal1
resistors, pins bmsb/blsb/vout, and the ladder's foot on ground (node 0 in
M8's subckt):

  xrmsb   bmsb -> vout   R   (row 0, left)
  xr2lsb  vout -> n0     2R  (row 0, middle)
  xr2msb  n0   -> 0      2R  (row 0, right)
  xrlsb   blsb -> n0     R   (standing on n0's pad)

Lengths and the shared width are the rung's current sizing
(sizing/sizing.yaml and the subckt's defaults: a 1:1 ratio that
`optimise.py numeric` is meant to correct). They are typed in here by the
layout-writer rather than read from the sizing file, so when the sizing
moves LVS reports this layout as out of date instead of silently
following it.

Each resistor is the PDK's draw_metal_res(res_type="rm1"): a metal1 bar
with the metal1_res marker (110/11) over its body. Magic only recognises
rm1 where the RESDEF marker (any 110/* datatype, res_mk 110/5 in the
klayout layer table) is present as well, so `resistor()` adds res_mk over
the same body. Terminals meet end to end on short, full-width pads: rm1 is
0.09 ohm/sq, so every square of routing is a fifth of a 5-square resistor
and would show up in pex_sim.

Ground is a metal1 label "0" on the drawing layer (34/0), not a pin label:
magic names the net 0 without making it a port, which is exactly M8's
subckt (0 is ngspice's global ground, not one of its pins).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "engine" / "lib"))
import layoutlib  # noqa: E402

L1 = layoutlib.GF180_LAYER["metal1"]
L1LBL = layoutlib.GF180_LAYER["metal1_label"]

CELL = "r2r_dac"
PINS = ["bmsb", "blsb", "vout"]
TAP_SIZE = 1.0
R_WIDTH = 2.0     # um, r_width=2e-6
R_LENGTH = 10.0   # um, r_length=1e-5
R2_LENGTH = 10.0  # um, r2_length=1e-5
GAP = 1.0         # um of metal1 pad between two resistor bodies
STUB = 0.28       # draw_metal_res's own metal1 extension past the marker


def resistor(top, length, x, y, vertical=False):
    """One rm1 with its body's lower-left corner at (x, y)."""
    _draw_fet, draw_res = layoutlib.gf180_cells()
    ref = top.add_ref(draw_res.draw_metal_res(
        l_res=length, w_res=R_WIDTH, res_type="rm1"))
    if vertical:
        ref.rotate(90)
        ref.move((x + R_WIDTH, y))
        body = (x, y, x + R_WIDTH, y + length)
    else:
        ref.move((x, y))
        body = (x, y, x + length, y + R_WIDTH)
    layoutlib.rect(top, *body, layoutlib.GF180_LAYER["res_mk"])
    return body


def generate():
    import gdsfactory as gf

    layoutlib.gf180_cells()
    top = gf.Component()
    labels = []
    w = R_WIDTH

    rmsb = resistor(top, R_LENGTH, 0.0, 0.0)
    x = rmsb[2] + GAP
    r2lsb = resistor(top, R2_LENGTH, x, 0.0)
    # n0's pad is as wide as a resistor, so xrlsb stands on it whole and
    # xr2msb starts right of it. With xrlsb's foot over xr2msb's body
    # instead, magic couples that body - a node no SPICE element reaches -
    # to n0, and ngspice finds a floating node.
    x = r2lsb[2] + w
    r2msb = resistor(top, R2_LENGTH, x, 0.0)
    n0_x = r2lsb[2]
    rlsb = resistor(top, R_LENGTH, n0_x, w + GAP, vertical=True)

    # bmsb: a pad left of xrmsb
    layoutlib.rect(top, -1.0, 0.0, 0.0, w, L1)
    labels.append(("bmsb", -0.5, w / 2, L1LBL))

    # vout: the pad between xrmsb and xr2lsb
    layoutlib.rect(top, rmsb[2], 0.0, r2lsb[0], w, L1)
    labels.append(("vout", (rmsb[2] + r2lsb[0]) / 2, w / 2, L1LBL))

    # n0: the pad between xr2lsb and xr2msb, extended up under xrlsb's foot
    layoutlib.rect(top, r2lsb[2], 0.0, r2msb[0], rlsb[1], L1)

    # blsb: a pad on top of xrlsb
    layoutlib.rect(top, rlsb[0], rlsb[3], rlsb[2], rlsb[3] + 1.0, L1)
    labels.append(("blsb", (rlsb[0] + rlsb[2]) / 2, rlsb[3] + 0.5, L1LBL))

    # ground: a pad right of xr2msb, named 0 but not a pin, running on to
    # a P+ substrate tap so the substrate the extracted capacitors land on
    # is ground and not a floating node
    tap_x = r2msb[2] + 2.0
    layoutlib.rect(top, r2msb[2], 0.0, tap_x + 0.8, w, L1)
    labels.append(("0", r2msb[2] + 0.5, w / 2, L1))
    tap = top.add_ref(layoutlib.psub_tap(TAP_SIZE))
    tap.move((tap_x, w / 2 - TAP_SIZE / 2))

    # magic numbers a cell's ports in the order their labels were written,
    # and a bench instantiates the extracted cell positionally: M8's order.
    labels.sort(key=lambda lbl: PINS.index(lbl[0]) if lbl[0] in PINS else 99)
    return layoutlib.finalize(top, CELL, labels)


if __name__ == "__main__":
    comp = generate()
    out = sys.argv[1] if len(sys.argv) > 1 else "r2r_dac.gds"
    comp.write_gds(out)
    print(f"wrote {out}")
