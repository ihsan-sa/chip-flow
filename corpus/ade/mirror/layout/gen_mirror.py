"""gen_mirror.py - layout generator for the ade/mirror corpus rung
(docs/design.md 5, "### M9."; docs/spikes/glayout.md's fallback).

Draws M8's own `current_mirror` (netlist/mirror.cir): xmref diode-connected
at W=4u, xmout at W=8u, both L=0.5u, sources and bulk on vss, pins
iref_node/iout/vdd/vss. vdd is a pin of M8's subckt that no device uses, so
here it is a labelled pad on its own.

Both devices are GF180's draw_nfet(). Their pads are read off the drawn
cell (layoutlib.fet_pads), so the wiring follows whatever W/L the constants
below say. The sizes are this generator's own, typed in by the
layout-writer; LVS is what checks them against netlist/mirror.cir.

Wiring, xmref at x=0 and xmout at x=MIRROR_DX, channels vertical:
  iref_node  a bus joining both bottom gate pads, plus xmref's drain
             dropped onto it (the diode connection)
  iout       xmout's drain pad, labelled in place
  vss        both source pads run up to a bus above the devices that also
             lands on one P+ substrate tap (DF.14 wants a tap within 20um)
  vdd        a lone pad right of the tap
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "engine" / "lib"))
import layoutlib  # noqa: E402

L1 = layoutlib.GF180_LAYER["metal1"]
L1LBL = layoutlib.GF180_LAYER["metal1_label"]

CELL = "current_mirror"
PINS = ["iref_node", "iout", "vdd", "vss"]
MREF_W = 4.0
MOUT_W = 8.0
GATE_L = 0.5
MIRROR_DX = 3.0
TAP_SIZE = 1.0


def shifted(box, dx):
    return (box[0] + dx, box[1], box[2] + dx, box[3])


def generate():
    import gdsfactory as gf

    draw_fet, _draw_res = layoutlib.gf180_cells()
    top = gf.Component()

    ref_cell = draw_fet.draw_nfet(l_gate=GATE_L, w_gate=MREF_W)
    out_cell = draw_fet.draw_nfet(l_gate=GATE_L, w_gate=MOUT_W)
    top.add_ref(ref_cell)
    top.add_ref(out_cell).move((MIRROR_DX, 0.0))
    ref = layoutlib.fet_pads(ref_cell)
    out = {k: shifted(v, MIRROR_DX) for k, v in layoutlib.fet_pads(out_cell).items()}

    labels = []

    # --- iref_node: bottom gate pads joined, xmref's drain dropped onto them
    g0, g1 = ref["gate_bot"], out["gate_bot"]
    layoutlib.rect(top, g0[0], g0[1], g1[2], g0[3], L1)
    d = ref["d"]
    layoutlib.rect(top, d[0], g0[1], d[2], d[1] + 0.2, L1)
    labels.append(("iref_node", (d[2] + g1[0]) / 2, (g0[1] + g0[3]) / 2, L1LBL))

    # --- iout: xmout's own drain pad
    d = out["d"]
    labels.append(("iout", (d[0] + d[2]) / 2, (d[1] + d[3]) / 2, L1LBL))

    # --- vss: both sources up to a bus above the taller device, and a tap
    bus_y0 = max(ref["gate_top"][3], out["gate_top"][3]) + 0.6
    bus_y1 = bus_y0 + 0.5
    for s in (ref["s"], out["s"]):
        layoutlib.rect(top, s[0], s[3] - 0.2, s[2], bus_y1, L1)
    tap_x = out["d"][2] + 2.0
    tap_y = (bus_y0 + bus_y1) / 2 - TAP_SIZE / 2
    tap = top.add_ref(layoutlib.psub_tap(TAP_SIZE))
    tap.move((tap_x, tap_y))
    layoutlib.rect(top, ref["s"][0], bus_y0, tap_x + 0.8, bus_y1, L1)
    labels.append(("vss", (ref["s"][2] + out["s"][0]) / 2,
                   (bus_y0 + bus_y1) / 2, L1LBL))

    # --- vdd: M8's pin with nothing on it inside the mirror
    vdd_x = tap_x + TAP_SIZE + 1.0
    layoutlib.rect(top, vdd_x, bus_y0, vdd_x + 1.0, bus_y1, L1)
    labels.append(("vdd", vdd_x + 0.5, (bus_y0 + bus_y1) / 2, L1LBL))

    # magic numbers a cell's ports in the order their labels were written,
    # and a bench instantiates the extracted cell positionally: M8's order.
    labels.sort(key=lambda lbl: PINS.index(lbl[0]))
    return layoutlib.finalize(top, CELL, labels)


if __name__ == "__main__":
    comp = generate()
    out_path = sys.argv[1] if len(sys.argv) > 1 else "mirror.gds"
    comp.write_gds(out_path)
    print(f"wrote {out_path}")
