"""gen_sensor_counted_analog.py - layout generator for the analog half of
the msde rung sensor_counted (docs/design.md 5, "Layout is code").

Draws netlist/sensor_counted_analog.cir: a NAND-enabled five-stage ring of
L=10um devices plus a short-channel output buffer, as a hard macro the
digital harden can place (docs/spikes/macro_harden/gen_inv.py's pin
conventions):

  vdd, vss   metal3 straps along the top and bottom, the full cell width,
             so the TT tile's vertical Metal4 PDN stripes cross both
  osc_en     metal2 track out to the cell's LEFT edge
  osc_out    metal2 track out to the cell's RIGHT edge

Nothing is drawn above metal3.

Floorplan: seven columns, left to right A E 1 2 3 4 B. Each column is a
pfet over an nfet, both turned so the channel (L) runs vertically: the pfet
by +90 (drain at its bottom, source and bulk tie at its top), the nfet by
-90 (drain at its top, source and bulk tie at its bottom). The drains meet
in a routing band between the two rows. In that band:

  drain link   metal1, the column's centre, pfet drain to nfet drain
  gate link    metal1, left of centre, pfet gate strip to nfet gate strip
  T1           metal2, stage k's drain link to stage k+1's gate link
               (A->1 via E's pfet drain, 1->2, 2->3, 3->4, 4->B, B->edge)
  T2           metal2, the feedback n4: column 4's drain link to A's gate
  T3           metal2, osc_en: E's gate link to the left edge
  nm           metal2, xmn0e's drain up the A|E gap to xmn0a's source

Column A is the NAND's ring half (xmp0a/xmn0a, gate n4), column E its
enable half (xmp0e/xmn0e, gate osc_en). xmn0a is the one device drawn
without a butted bulk tie: its source is the stack node nm, not vss.
Every pfet sits in one merged nwell tied to vdd.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "engine" / "lib"))
import layoutlib  # noqa: E402

LY = dict(layoutlib.GF180_LAYER)
LY.update({"via2": (38, 0), "metal3": (42, 0), "metal3_label": (42, 10)})
M1, M2, M3 = LY["metal1"], LY["metal2"], LY["metal3"]
PR_BNDRY = (0, 0)

CELL = "sensor_counted_analog"
PINS = ["osc_en", "osc_out", "vdd", "vss"]

RING_L = 10.0
BUF_L = 0.5
# (column, pfet (ref, W, L, bulk tie), nfet (ref, W, L, bulk tie))
COLUMNS = [
    ("A", ("xmp0a", 2.0, RING_L, True), ("xmn0a", 2.0, RING_L, False)),
    ("E", ("xmp0e", 2.0, RING_L, True), ("xmn0e", 2.0, RING_L, True)),
    ("1", ("xmp1", 2.0, RING_L, True), ("xmn1", 1.0, RING_L, True)),
    ("2", ("xmp2", 2.0, RING_L, True), ("xmn2", 1.0, RING_L, True)),
    ("3", ("xmp3", 2.0, RING_L, True), ("xmn3", 1.0, RING_L, True)),
    ("4", ("xmp4", 2.0, RING_L, True), ("xmn4", 1.0, RING_L, True)),
    ("B", ("xmpb", 2.0, BUF_L, True), ("xmnb", 1.0, BUF_L, True)),
]
PITCH = 6.6       # column centre to centre
X0 = 3.0          # first column's centre
BAND = 4.0        # nfet drain top to pfet drain bottom
VIA = 0.26        # GF180 via1/via2 are a fixed 0.26 square
PAD = 0.5         # metal landing around each via
LINK = 0.38       # drain link width, the pads' own width
RAIL = 1.0        # metal1 rail and metal3 strap height
MIN_W = 45.2      # >= the TT PDN's 38.87um pitch plus one VPWR/VGND pair


def via(top, cx, cy, layer_via, layers_metal):
    h = VIA / 2
    layoutlib.rect(top, cx - h, cy - h, cx + h, cy + h, LY[layer_via])
    p = PAD / 2
    for m in layers_metal:
        layoutlib.rect(top, cx - p, cy - p, cx + p, cy + p, LY[m])


def via12(top, cx, cy):
    via(top, cx, cy, "via1", ("metal1", "metal2"))


def rot_box(b, angle):
    x0, y0, x1, y1 = b
    if angle == 90:     # (x, y) -> (-y, x)
        return (-y1, x0, -y0, x1)
    if angle == -90:    # (x, y) -> (y, -x)
        return (y0, -x1, y1, -x0)
    raise ValueError(angle)


def move_box(b, dx, dy):
    return (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)


def local_pads(cell, bulk_tie):
    """The FET's metal1 pads in its own (unrotated) frame: d is the left
    diffusion pad, s the right one (butted to the tap when there is one),
    gate_bot/gate_top the gate contacts below and above the channel."""
    if not bulk_tie:
        # fet_pads() names the left diffusion pad "s"; here the left one is
        # always "d", the pad place() turns to face the routing band
        fp = layoutlib.fet_pads(cell)
        return {"d": fp["s"], "s": fp["d"], "gate_bot": fp["gate_bot"],
                "gate_top": fp["gate_top"]}
    boxes = layoutlib.layer_boxes(cell, M1)
    if len(boxes) != 5:
        raise layoutlib.LayoutError(
            f"expected 5 metal1 pads on a bulk-tied FET, got {len(boxes)}")
    d, g0, g1, s, t = sorted(boxes, key=lambda r: (r[0], r[1]))
    gb, gt = sorted((g0, g1), key=lambda r: r[1])
    return {"d": d, "gate_bot": gb, "gate_top": gt, "s": s, "tap": t}


def place(top, cell, bulk_tie, angle, cx, drain_edge):
    """Add `cell` rotated by `angle` so its drain pad is centred on x=cx,
    with the drain pad's inner edge (bottom for the pfet at +90, top for
    the nfet at -90) at y=drain_edge. Returns its pads in the top frame,
    gates as 'gl'/'gr' (left/right of the drain)."""
    pads = {k: rot_box(v, angle) for k, v in local_pads(cell, bulk_tie).items()}
    d = pads["d"]
    dx = cx - (d[0] + d[2]) / 2
    dy = drain_edge - (d[1] if angle == 90 else d[3])
    ref = top.add_ref(cell)
    ref.rotate(angle)
    ref.move((dx, dy))
    pads = {k: move_box(v, dx, dy) for k, v in pads.items()}
    g = sorted((pads.pop("gate_bot"), pads.pop("gate_top")), key=lambda r: r[0])
    pads["gl"], pads["gr"] = g
    return pads


def generate():
    import gdsfactory as gf

    draw_fet, _ = layoutlib.gf180_cells()
    top = gf.Component()
    labels = []

    y_nd = 0.0            # nfet drain pads' top edge
    y_pd = y_nd + BAND    # pfet drain pads' bottom edge
    t1, t2, t3 = y_nd + 2.9, y_nd + 2.0, y_nd + 1.1

    cols = {}
    for i, (name, (pref, pw, pl, ptie), (nref, nw, nl, ntie)) in enumerate(COLUMNS):
        cx = X0 + i * PITCH
        pcell = draw_fet.draw_pfet(l_gate=pl, w_gate=pw,
                                   bulk="Bulk Tie" if ptie else "None")
        ncell = draw_fet.draw_nfet(l_gate=nl, w_gate=nw,
                                   bulk="Bulk Tie" if ntie else "None")
        p = place(top, pcell, ptie, 90, cx, y_pd)
        n = place(top, ncell, ntie, -90, cx, y_nd)
        cols[name] = {"cx": cx, "p": p, "n": n}

        # gate link, left of the drain: down from the pfet's left gate pad,
        # a block through the band, down onto the nfet's left gate pad
        pg, ng = p["gl"], n["gl"]
        band_top = p["d"][1] - 0.35
        band_bot = n["d"][1]
        layoutlib.rect(top, pg[0], band_top, pg[2], pg[1] + 0.2, M1)
        bx0, bx1 = min(pg[0], ng[0]), max(pg[2], ng[2])
        layoutlib.rect(top, bx0, band_bot, bx1, band_top, M1)
        layoutlib.rect(top, ng[0], ng[3] - 0.2, ng[2], band_bot, M1)
        cols[name]["gx"] = (bx0 + bx1) / 2

        # drain link (column E's two drains are different nets: n0, nm)
        if name != "E":
            layoutlib.rect(top, cx - LINK / 2, n["d"][1], cx + LINK / 2,
                           p["d"][3], M1)
        else:
            layoutlib.rect(top, cx - LINK / 2, t1 - PAD / 2, cx + LINK / 2,
                           p["d"][3], M1)

    # T1: n0 (A, E's pfet drain) -> 1, then k -> k+1, 4 -> B, B -> edge
    order = [c[0] for c in COLUMNS]
    def link(src, dst, y):
        xs, xd = cols[src]["cx"], cols[dst]["gx"]
        via12(top, xs, y)
        via12(top, xd, y)
        layoutlib.rect(top, min(xs, xd) - PAD / 2, y - PAD / 2,
                       max(xs, xd) + PAD / 2, y + PAD / 2, M2)
    # n0: A's drain link, E's pfet drain, 1's gate
    via12(top, cols["E"]["cx"], t1)
    link("A", "1", t1)
    for a, b in zip(order[2:], order[3:]):
        link(a, b, t1)
    # T2: feedback n4 -> A's gate
    link("4", "A", t2)

    # T3: osc_en, E's gate link out to the left edge
    ex = cols["E"]["gx"]
    via12(top, ex, t3)
    layoutlib.rect(top, 0.0, t3 - PAD / 2, ex + PAD / 2, t3 + PAD / 2, M2)
    labels.append(("osc_en", 0.5, t3, LY["metal2_label"]))

    # osc_out: B's drain link out to the right edge
    x_right = max(X0 + (len(COLUMNS) - 1) * PITCH + X0, MIN_W)
    bx = cols["B"]["cx"]
    via12(top, bx, t1)
    layoutlib.rect(top, bx - PAD / 2, t1 - PAD / 2, x_right, t1 + PAD / 2, M2)
    labels.append(("osc_out", x_right - 0.5, t1, LY["metal2_label"]))

    # nm: xmn0e's drain -> up the A|E gap -> xmn0a's source
    na, ne = cols["A"]["n"], cols["E"]["n"]
    ymn = (ne["d"][1] + ne["d"][3]) / 2
    ysa = (na["s"][1] + na["s"][3]) / 2
    gap_x = (cols["A"]["cx"] + cols["E"]["cx"]) / 2
    via12(top, cols["E"]["cx"], ymn)
    via12(top, cols["A"]["cx"], ysa)
    layoutlib.rect(top, gap_x - PAD / 2, ymn - PAD / 2,
                   cols["E"]["cx"] + PAD / 2, ymn + PAD / 2, M2)
    layoutlib.rect(top, gap_x - PAD / 2, ysa - PAD / 2, gap_x + PAD / 2,
                   ymn + PAD / 2, M2)
    layoutlib.rect(top, cols["A"]["cx"] - PAD / 2, ysa - PAD / 2,
                   gap_x + PAD / 2, ysa + PAD / 2, M2)

    # vdd: every pfet's source + tap up to a metal1 rail, a metal3 strap on
    # top of it, one via1+via2 stack per column
    vdd_y0 = max(c["p"]["tap"][3] for c in cols.values()) + 0.6
    vdd_y1 = vdd_y0 + RAIL
    vss_y1 = min(c["n"]["tap"][1] for c in cols.values()
                 if "tap" in c["n"]) - 0.6
    vss_y0 = vss_y1 - RAIL
    for name, c in cols.items():
        s, t = c["p"]["s"], c["p"]["tap"]
        layoutlib.rect(top, min(s[0], t[0]), s[1], max(s[2], t[2]), vdd_y1, M1)
        vx, vy = (s[0] + s[2]) / 2, (vdd_y0 + vdd_y1) / 2
        via(top, vx, vy, "via1", ("metal1", "metal2"))
        via(top, vx, vy, "via2", ("metal2", "metal3"))
        if "tap" not in c["n"]:
            continue    # xmn0a: its source is nm, not vss
        s, t = c["n"]["s"], c["n"]["tap"]
        layoutlib.rect(top, min(s[0], t[0]), vss_y0, max(s[2], t[2]), s[3], M1)
        vx, vy = (s[0] + s[2]) / 2, (vss_y0 + vss_y1) / 2
        via(top, vx, vy, "via1", ("metal1", "metal2"))
        via(top, vx, vy, "via2", ("metal2", "metal3"))
    for y0, y1, net in ((vdd_y0, vdd_y1, "vdd"), (vss_y0, vss_y1, "vss")):
        layoutlib.rect(top, 0.0, y0, x_right, y1, M1)
        layoutlib.rect(top, 0.0, y0, x_right, y1, M3)
        labels.append((net, 1.0, (y0 + y1) / 2, LY["metal3_label"]))

    # one nwell over every pfet (all of them are bulk vdd), so no two
    # columns' wells sit at a spacing the deck has to judge
    nw = layoutlib.layer_boxes(top, LY["nwell"])
    layoutlib.rect(top, min(b[0] for b in nw), min(b[1] for b in nw),
                   max(b[2] for b in nw), max(b[3] for b in nw), LY["nwell"])

    # move the drawn lower-left to the origin, add the PR boundary
    # LibreLane's Magic.StreamOut reads a macro's size from (gen_inv.py)
    bb = top.dbbox()
    dx, dy = -bb.left, -bb.bottom
    shifted = gf.Component()
    shifted.add_ref(top).move((dx, dy))
    layoutlib.rect(shifted, 0.0, 0.0, bb.width(), bb.height(), PR_BNDRY)
    labels = [(txt, x + dx, y + dy, lay) for txt, x, y, lay in labels]
    labels.sort(key=lambda lbl: PINS.index(lbl[0]))
    return layoutlib.finalize(shifted, CELL, labels)


if __name__ == "__main__":
    comp = generate()
    out_path = sys.argv[1] if len(sys.argv) > 1 else f"{CELL}.gds"
    comp.write_gds(out_path)
    print(f"wrote {out_path} bbox={comp.dbbox()}")
