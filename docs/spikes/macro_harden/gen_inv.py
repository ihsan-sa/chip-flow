"""gen_inv.py - a tiny GF180 CMOS inverter, drawn as a hard macro for the
macro_harden spike (docs/spikes/macro_harden.md).

    eda python gen_inv.py OUT.gds

Built with the repo's own layout code (engine/lib/layoutlib.py), the same
way corpus/ade/mirror/layout/gen_mirror.py is: the PDK's draw_nfet/draw_pfet
(both with a butted "Bulk Tie", so each device carries its own tap), wired
by hand on metal1, then brought up to macro pins:

  in   metal2 bar from the joined gates to the cell's RIGHT edge
  out  metal2 bar from the joined drains to the cell's LEFT edge
  vss  metal3 strap along the bottom, the full cell width
  vdd  metal3 strap along the top, the full cell width

The straps span the whole width on purpose. The TT tile's power grid is
vertical Metal4 stripes only (FP_PDN_MULTILAYER 0) at a 38.87um pitch, VPWR
and VGND 3.3um apart, so a cell at least one pitch plus one stripe pair wide
(STRAP_W below) is crossed by at least one VPWR and one VGND stripe wherever
it is placed, and pdngen can drop a via3 where each stripe crosses the
matching strap. The lower-left of the drawn geometry is moved to (0, 0) so
the GDS and the LEF magic writes from it share an origin.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "engine" / "lib"))
import layoutlib  # noqa: E402

LY = dict(layoutlib.GF180_LAYER)
LY.update({"via2": (38, 0), "metal3": (42, 0), "metal3_label": (42, 10)})

PR_BNDRY = (0, 0)

CELL = "inv_macro"
PINS = ["in", "out", "vdd", "vss"]
NW, PW, GL = 2.0, 4.0, 0.5   # nfet W, pfet W, both L (um)
DY = 5.0                    # pfet origin above the nfet's
STRAP_W = 45.0              # >= 38.87 pitch + 1.6 + 1.7 + 1.6 stripe pair
X0 = 20.0                   # where the device pair sits along the straps
VIA = 0.26                  # GF180 via1/via2 are a fixed 0.26 square
PAD = 0.5                   # metal landing around each via


def via(top, cx, cy, layer_via, layers_metal):
    h = VIA / 2
    layoutlib.rect(top, cx - h, cy - h, cx + h, cy + h, LY[layer_via])
    p = PAD / 2
    for m in layers_metal:
        layoutlib.rect(top, cx - p, cy - p, cx + p, cy + p, LY[m])


def generate():
    import gdsfactory as gf

    draw_fet, _ = layoutlib.gf180_cells()
    top = gf.Component()
    ncell = draw_fet.draw_nfet(l_gate=GL, w_gate=NW, bulk="Bulk Tie")
    pcell = draw_fet.draw_pfet(l_gate=GL, w_gate=PW, bulk="Bulk Tie")
    top.add_ref(ncell).move((X0, 0.0))
    top.add_ref(pcell).move((X0, DY))

    def pads(cell, dx, dy):
        # five metal1 pads, sorted by x then y: drain (left), gate_bot,
        # gate_top, source (right, butted to the tap), tap
        boxes = layoutlib.layer_boxes(cell, LY["metal1"])
        if len(boxes) != 5:
            raise layoutlib.LayoutError(
                f"expected 5 metal1 pads on a bulk-tied FET, got {len(boxes)}")
        b = [(x0 + dx, y0 + dy, x1 + dx, y1 + dy) for x0, y0, x1, y1 in boxes]
        d, g0, g1, s, t = sorted(b, key=lambda r: (r[0], r[1]))
        gb, gt = sorted((g0, g1), key=lambda r: r[1])
        return {"d": d, "gate_bot": gb, "gate_top": gt, "s": s, "tap": t}

    n = pads(ncell, X0, 0.0)
    p = pads(pcell, X0, DY)
    M1 = LY["metal1"]
    M2 = LY["metal2"]
    M3 = LY["metal3"]
    labels = []

    # in: nfet top gate pad up to the pfet bottom gate pad, then a metal2
    # bar out to the right edge
    g = n["gate_top"]
    layoutlib.rect(top, g[0], g[1], g[2], p["gate_bot"][3], M1)
    in_y = (n["gate_top"][3] + p["gate_bot"][1]) / 2
    in_x = n["tap"][2] + 1.0
    layoutlib.rect(top, g[0], in_y - PAD / 2, in_x + PAD / 2, in_y + PAD / 2, M1)
    via(top, in_x, in_y, "via1", ("metal1", "metal2"))
    layoutlib.rect(top, in_x - PAD / 2, in_y - PAD / 2, STRAP_W, in_y + PAD / 2, M2)
    labels.append(("in", STRAP_W - 0.5, in_y, LY["metal2_label"]))

    # out: both drains joined by a metal1 strip, then metal2 to the left edge
    dn, dp = n["d"], p["d"]
    ox0, ox1 = min(dn[0], dp[0]), max(dn[2], dp[2])
    layoutlib.rect(top, ox0, dn[1], ox1, dp[3], M1)
    out_y = in_y
    out_x = ox0 - 1.0
    layoutlib.rect(top, out_x - PAD / 2, out_y - PAD / 2, ox1, out_y + PAD / 2, M1)
    via(top, out_x, out_y, "via1", ("metal1", "metal2"))
    layoutlib.rect(top, 0.0, out_y - PAD / 2, out_x + PAD / 2, out_y + PAD / 2, M2)
    labels.append(("out", 0.5, out_y, LY["metal2_label"]))

    # vss: nfet source + tap bridged, down to a metal3 strap
    s, t = n["s"], n["tap"]
    layoutlib.rect(top, s[0], s[1], t[2], s[3], M1)
    vss_y0 = n["gate_bot"][1] - 2.0
    vss_y1 = vss_y0 + 1.0
    layoutlib.rect(top, s[0], vss_y0, t[2], s[1], M1)
    vx = (s[0] + t[2]) / 2
    vy = (vss_y0 + vss_y1) / 2
    via(top, vx, vy, "via1", ("metal1", "metal2"))
    via(top, vx, vy, "via2", ("metal2", "metal3"))
    layoutlib.rect(top, 0.0, vss_y0, STRAP_W, vss_y1, M3)
    labels.append(("vss", 1.0, vy, LY["metal3_label"]))

    # vdd: pfet source + tap bridged, up to a metal3 strap
    s, t = p["s"], p["tap"]
    layoutlib.rect(top, s[0], s[1], t[2], s[3], M1)
    vdd_y0 = p["gate_top"][3] + 1.6
    vdd_y1 = vdd_y0 + 1.0
    layoutlib.rect(top, s[0], s[3], t[2], vdd_y1, M1)
    vx = (s[0] + t[2]) / 2
    vy = (vdd_y0 + vdd_y1) / 2
    via(top, vx, vy, "via1", ("metal1", "metal2"))
    via(top, vx, vy, "via2", ("metal2", "metal3"))
    layoutlib.rect(top, 0.0, vdd_y0, STRAP_W, vdd_y1, M3)
    labels.append(("vdd", 1.0, vy, LY["metal3_label"]))

    # move the drawn lower-left to the origin (the pfet's nwell and the
    # nfet's implant reach past the straps' own y range)
    bb = top.dbbox()
    dx, dy = -bb.left, -bb.bottom
    shifted = gf.Component()
    shifted.add_ref(top).move((dx, dy))
    # PR boundary (GDS 0/0, magic's PRBNDRY -> FIXED_BBOX): LibreLane's
    # Magic.StreamOut reads a macro's size from it and stops with "Failed
    # to extract PR boundary from GDSII view of macro" when it is missing.
    layoutlib.rect(shifted, 0.0, 0.0, bb.width(), bb.height(), PR_BNDRY)
    labels = [(txt, x + dx, y + dy, lay) for txt, x, y, lay in labels]
    labels.sort(key=lambda lbl: PINS.index(lbl[0]))
    return layoutlib.finalize(shifted, CELL, labels)


if __name__ == "__main__":
    comp = generate()
    out_path = sys.argv[1] if len(sys.argv) > 1 else f"{CELL}.gds"
    comp.write_gds(out_path)
    print(f"wrote {out_path} bbox={comp.dbbox()}")
