"""gen_dac_tile_analog.py - layout generator for the analog half of the
msde rung dac_tile (docs/design.md 5, "Layout is code").

Draws netlist/dac_tile_analog.cir: two CMOS inverters driving a 2-bit R-2R
ladder of ppolyf_u_1k resistors, as a hard macro in the pin conventions of
corpus/msde/sensor_counted/analog/layout/gen_sensor_counted_analog.py:

  vdd, vss     metal3 straps along the top and bottom, the full cell width
  bmsb, blsb   metal2 tracks out to the cell's LEFT edge
  vout         a metal2 track out to the cell's RIGHT edge

Nothing is drawn above metal3.

Floorplan. On the left, two inverter columns M and L, each a pfet over an
nfet turned and joined exactly as in sensor_counted's generator (drain link
at the column's centre, gate link left of it, both metal1 through a
routing band between the two rows). The band is tall here because two of
its metal2 tracks run straight on into the ladder:

  bmsb, blsb   metal2, the left edge to column M's / L's gate link
  dm, dl       metal2, column M's / L's drain link to its ladder leg

On the right, the four resistors lie in horizontal rows, bottom to top:

  row 0  xrmsb   R   dm   | vout      (its row centre is dm's track)
  row 1  xr2lsb  2R  vout | n0
  row 2  xrlsb   R   dl   | n0        (its row centre is dl's track)
  row 3  xr2msb  2R  vss  | n0

vout is a metal1 line in the gap between rows 0 and 1, from row 1's left
terminal past row 0's right one to the right edge. n0 is a metal2 spine
down the right terminals of rows 1-3. Each resistor carries its own
substrate contact left of its body; a metal1 strip joins the four to the
vss rail, and row 3's left terminal is butted onto its contact.

For the hardened tile's own checks, each resistor's terminal contacts sit
where magic's HRES.7 wants them (magic_clean_res), and dummy COMP squares
fill the empty corner under the 2R rows (DCF.1a in TT's precheck).
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

CELL = "dac_tile_analog"
PINS = ["bmsb", "blsb", "vout", "vdd", "vss"]

# (column, pfet (ref, W, L), nfet (ref, W, L)), every device bulk-tied
FET_L = 0.5
COLUMNS = [
    ("M", ("xmpm", 4.0, FET_L), ("xmnm", 2.0, FET_L)),
    ("L", ("xmpl", 4.0, FET_L), ("xmnl", 2.0, FET_L)),
]
# (row, ref, length) bottom to top; r_width shared
R_WIDTH = 2.0     # um, r_width=2e-6
R_LENGTH = 10.0   # um, r_length=1e-5
R2_LENGTH = 20.0  # um, r2_length=2e-5
ROWS = [("xrmsb", R_LENGTH), ("xr2lsb", R2_LENGTH),
        ("xrlsb", R_LENGTH), ("xr2msb", R2_LENGTH)]
ROW_PITCH = 4.0   # row centre to row centre

PITCH = 7.0       # inverter column centre to centre
X0 = 3.0          # first column's centre
LADDER_GAP = 5.0  # last column's centre to the resistors' left terminal
VIA = 0.26        # GF180 via1/via2 are a fixed 0.26 square
PAD = 0.5         # metal landing around each via
COMP_DUMMY = (22, 4)
DUMMY = 5.0         # dummy COMP square, the PDK fill script's (DCF.1c)
DUMMY_GAP = 1.9     # DCF.2b: dummy to dummy
DUMMY_SPACE = 3.5   # DCF.8a: dummy to the resistor marking layer
LINK = 0.38       # drain link width
RAIL = 1.0        # metal1 rail and metal3 strap height
WIRE = 0.3        # metal1 vout line width
MIN_W = 45.2      # >= the TT PDN's 38.87um pitch plus one VPWR/VGND pair

# band tracks, measured up from the nfet drains' top edge
T_BMSB, T_BLSB, T_DM = 1.0, 2.0, 3.2
T_DL = T_DM + 2 * ROW_PITCH
BAND = T_DL + 1.0


def via(top, cx, cy, layer_via, layers_metal):
    h = VIA / 2
    layoutlib.rect(top, cx - h, cy - h, cx + h, cy + h, LY[layer_via])
    p = PAD / 2
    for m in layers_metal:
        layoutlib.rect(top, cx - p, cy - p, cx + p, cy + p, LY[m])


def via12(top, cx, cy):
    via(top, cx, cy, "via1", ("metal1", "metal2"))


def hwire(top, x0, x1, y, layer):
    layoutlib.rect(top, min(x0, x1) - PAD / 2, y - PAD / 2,
                   max(x0, x1) + PAD / 2, y + PAD / 2, layer)


def vwire(top, x, y0, y1, layer):
    layoutlib.rect(top, x - PAD / 2, min(y0, y1) - PAD / 2,
                   x + PAD / 2, max(y0, y1) + PAD / 2, layer)


def centre(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def rot_box(b, angle):
    x0, y0, x1, y1 = b
    if angle == 90:     # (x, y) -> (-y, x)
        return (-y1, x0, -y0, x1)
    if angle == -90:    # (x, y) -> (y, -x)
        return (y0, -x1, y1, -x0)
    raise ValueError(angle)


def move_box(b, dx, dy):
    return (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)


def local_pads(cell):
    """A bulk-tied FET's metal1 pads in its own frame: d the left
    diffusion pad, s the right one (butted to the tap), gate_bot/gate_top
    the gate contacts below and above the channel."""
    boxes = layoutlib.layer_boxes(cell, M1)
    if len(boxes) != 5:
        raise layoutlib.LayoutError(
            f"expected 5 metal1 pads on a bulk-tied FET, got {len(boxes)}")
    d, g0, g1, s, t = sorted(boxes, key=lambda r: (r[0], r[1]))
    gb, gt = sorted((g0, g1), key=lambda r: r[1])
    return {"d": d, "gate_bot": gb, "gate_top": gt, "s": s, "tap": t}


def place(top, cell, angle, cx, drain_edge):
    """sensor_counted's place(): `cell` rotated by `angle` with its drain
    pad centred on x=cx and the pad's inner edge at y=drain_edge. Returns
    its pads in the top frame, gates as 'gl'/'gr'."""
    pads = {k: rot_box(v, angle) for k, v in local_pads(cell).items()}
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


# The PDK's klayout cell puts each terminal's contacts 0.25um from the SAB
# block (0.35um in from the poly end, SAB 0.1um past the body). klayout's
# PRES/LRES/HRES.7 is a minimum 0.22um, which that meets; magic's reading
# of the same rule is exact (SAB + 0.22um + one 0.22um contact), so the
# contacts' outer 0.03um is a magic DRC error in the hardened tile. Moving
# each terminal's contacts 0.03um toward the body meets both decks.
RES_CO_SHIFT = 0.03


def magic_clean_res(cell, length):
    """A flat copy of a ppolyf_u_high_Rs cell with its terminal contacts
    RES_CO_SHIFT toward the body; the substrate tap's contact stays."""
    import gdsfactory as gf
    import klayout.db as kdb

    out = gf.Component()
    out.add_ref(cell)
    out.flatten()
    shapes = out.shapes(out.kcl.layer(*LY["contact"]))
    moved = 0
    for shape in list(shapes.each()):
        box = shape.dbbox()
        if box.left > -1.0 and box.right < 0:
            dx = RES_CO_SHIFT
        elif box.left > length and box.right < length + 1.0:
            dx = -RES_CO_SHIFT
        else:
            continue
        shape.transform(kdb.DTrans(dx, 0))
        moved += 1
    if moved == 0:
        raise layoutlib.LayoutError("no terminal contacts on a ppolyf_u_1k")
    return out


def resistor(top, length, x, y):
    """One ppolyf_u_1k with its body's lower-left corner at (x, y), the
    body running right. Returns its metal1 pads: 'sub' (its substrate
    contact), 'a' (left terminal) and 'b' (right terminal)."""
    _draw_fet, draw_res = layoutlib.gf180_cells()
    cell = magic_clean_res(
        draw_res.draw_ppolyf_u_high_Rs_res(l_res=length, w_res=R_WIDTH),
        length)
    boxes = layoutlib.layer_boxes(cell, M1)
    if len(boxes) != 3:
        raise layoutlib.LayoutError(
            f"expected 3 metal1 pads on a ppolyf_u_1k, got {len(boxes)}")
    sub, a, b = sorted(boxes, key=lambda r: r[0])
    top.add_ref(cell).move((x, y))
    bb = cell.dbbox()
    return {"sub": move_box(sub, x, y), "a": move_box(a, x, y),
            "b": move_box(b, x, y),
            "box": (bb.left + x, bb.bottom + y, bb.right + x, bb.top + y)}


def generate():
    import gdsfactory as gf

    draw_fet, _ = layoutlib.gf180_cells()
    top = gf.Component()
    labels = []

    y_nd = 0.0            # nfet drain pads' top edge
    y_pd = y_nd + BAND    # pfet drain pads' bottom edge

    cols = {}
    for i, (name, (pref, pw, pl), (nref, nw, nl)) in enumerate(COLUMNS):
        cx = X0 + i * PITCH
        p = place(top, draw_fet.draw_pfet(l_gate=pl, w_gate=pw, bulk="Bulk Tie"),
                  90, cx, y_pd)
        n = place(top, draw_fet.draw_nfet(l_gate=nl, w_gate=nw, bulk="Bulk Tie"),
                  -90, cx, y_nd)
        cols[name] = {"cx": cx, "p": p, "n": n}
        # gate link, left of the drain
        pg, ng = p["gl"], n["gl"]
        band_top = p["d"][1] - 0.35
        band_bot = n["d"][1]
        layoutlib.rect(top, pg[0], band_top, pg[2], pg[1] + 0.2, M1)
        bx0, bx1 = min(pg[0], ng[0]), max(pg[2], ng[2])
        layoutlib.rect(top, bx0, band_bot, bx1, band_top, M1)
        layoutlib.rect(top, ng[0], ng[3] - 0.2, ng[2], band_bot, M1)
        cols[name]["gx"] = (bx0 + bx1) / 2
        # drain link
        layoutlib.rect(top, cx - LINK / 2, n["d"][1], cx + LINK / 2,
                       p["d"][3], M1)

    # the ladder: row k's centre sits on dm's track plus k row pitches
    fet_right = max(max(c["p"]["tap"][2], c["n"]["tap"][2], c["p"]["s"][2])
                    for c in cols.values())
    rx = max(cols["L"]["cx"] + LADDER_GAP, fet_right + 4.0)
    rows = []
    for k, (_ref, length) in enumerate(ROWS):
        y0 = y_nd + T_DM + k * ROW_PITCH - R_WIDTH / 2
        rows.append(resistor(top, length, rx, y0))

    # inputs: metal2 from the left edge to each column's gate link
    for name, t, pin in (("M", T_BMSB, "bmsb"), ("L", T_BLSB, "blsb")):
        gx = cols[name]["gx"]
        via12(top, gx, y_nd + t)
        layoutlib.rect(top, 0.0, y_nd + t - PAD / 2, gx + PAD / 2,
                       y_nd + t + PAD / 2, M2)
        labels.append((pin, 0.5, y_nd + t, LY["metal2_label"]))

    # dm, dl: each drain link straight across on metal2 to its leg
    for name, t, row in (("M", T_DM, 0), ("L", T_DL, 2)):
        ax, ay = centre(rows[row]["a"])
        cx = cols[name]["cx"]
        via12(top, cx, ay)
        via12(top, ax, ay)
        hwire(top, cx, ax, ay, M2)

    # vout: a metal1 line in the gap between rows 0 and 1, joining row 1's
    # left terminal and row 0's right one, on to a metal2 pin at the right
    right = max(r["box"][2] for r in rows)
    x_right = max(right + 2.0, MIN_W)
    y_v = (rows[0]["box"][3] + rows[1]["box"][1]) / 2
    a1, b0 = rows[1]["a"], rows[0]["b"]
    layoutlib.rect(top, a1[0], y_v - WIRE / 2, x_right - 0.5, y_v + WIRE / 2, M1)
    layoutlib.rect(top, a1[0], y_v - WIRE / 2, a1[2], a1[1] + 0.2, M1)
    layoutlib.rect(top, b0[0], b0[3] - 0.2, b0[2], y_v + WIRE / 2, M1)
    via12(top, x_right - 0.75, y_v)
    layoutlib.rect(top, x_right - 1.0, y_v - PAD / 2, x_right, y_v + PAD / 2, M2)
    labels.append(("vout", x_right - 0.25, y_v, LY["metal2_label"]))

    # n0: a metal2 spine down rows 1 and 3's right terminals, row 2's
    # joined to it across
    spine_x = centre(rows[1]["b"])[0]
    for k in (1, 2, 3):
        via12(top, *centre(rows[k]["b"]))
    vwire(top, spine_x, centre(rows[1]["b"])[1], centre(rows[3]["b"])[1], M2)
    bx2, by2 = centre(rows[2]["b"])
    hwire(top, bx2, spine_x, by2, M2)

    # vss rail below the nfets, vdd rail above the pfets and the ladder
    vdd_y0 = max(max(c["p"]["tap"][3] for c in cols.values()),
                 max(r["box"][3] for r in rows)) + 0.6
    vdd_y1 = vdd_y0 + RAIL
    vss_y1 = min(c["n"]["tap"][1] for c in cols.values()) - 0.6
    vss_y0 = vss_y1 - RAIL
    for c in cols.values():
        s, t = c["p"]["s"], c["p"]["tap"]
        layoutlib.rect(top, min(s[0], t[0]), s[1], max(s[2], t[2]), vdd_y1, M1)
        vx, vy = (s[0] + s[2]) / 2, (vdd_y0 + vdd_y1) / 2
        via(top, vx, vy, "via1", ("metal1", "metal2"))
        via(top, vx, vy, "via2", ("metal2", "metal3"))
        s, t = c["n"]["s"], c["n"]["tap"]
        layoutlib.rect(top, min(s[0], t[0]), vss_y0, max(s[2], t[2]), s[3], M1)
        vx, vy = (s[0] + s[2]) / 2, (vss_y0 + vss_y1) / 2
        via(top, vx, vy, "via1", ("metal1", "metal2"))
        via(top, vx, vy, "via2", ("metal2", "metal3"))

    # the ladder's substrate contacts down a metal1 strip to the vss rail;
    # row 3's left terminal (the ladder's foot) butted onto its contact
    sub3 = rows[3]["sub"]
    layoutlib.rect(top, sub3[0], vss_y0, sub3[2], sub3[3], M1)
    layoutlib.rect(top, sub3[0], sub3[1], rows[3]["a"][2], sub3[3], M1)
    vx = (sub3[0] + sub3[2]) / 2
    via(top, vx, (vss_y0 + vss_y1) / 2, "via1", ("metal1", "metal2"))
    via(top, vx, (vss_y0 + vss_y1) / 2, "via2", ("metal2", "metal3"))

    for y0, y1, net in ((vdd_y0, vdd_y1, "vdd"), (vss_y0, vss_y1, "vss")):
        layoutlib.rect(top, 0.0, y0, x_right, y1, M1)
        layoutlib.rect(top, 0.0, y0, x_right, y1, M3)
        labels.append((net, 1.0, (y0 + y1) / 2, LY["metal3_label"]))

    # dummy COMP under row 1's right half, where row 0 is shorter: left
    # empty, that corner and the 10um stdcell-row cut around the macro form
    # an area with no COMP within 10um (DCF.1a in the tile's precheck).
    # Kept DCF.8a's 3.5um from the rows' resistor layers (which enclose
    # their poly2, so DCF.5's 1.5um holds too); the circuit COMP (DCF.4,
    # 3.5um) is all left of the ladder.
    fill_box = (rows[0]["box"][2] + DUMMY_SPACE, vss_y0,
                x_right, rows[1]["box"][1] - DUMMY_SPACE)
    fills = 0
    x = fill_box[0]
    while x + DUMMY <= fill_box[2]:
        y = fill_box[1]
        while y + DUMMY <= fill_box[3]:
            layoutlib.rect(top, x, y, x + DUMMY, y + DUMMY, COMP_DUMMY)
            fills += 1
            y += DUMMY + DUMMY_GAP
        x += DUMMY + DUMMY_GAP
    if fills == 0:
        raise layoutlib.LayoutError(
            f"no room for dummy COMP under the ladder: {fill_box}")

    # one nwell over both pfets (both bulk vdd)
    nw = layoutlib.layer_boxes(top, LY["nwell"])
    layoutlib.rect(top, min(b[0] for b in nw), min(b[1] for b in nw),
                   max(b[2] for b in nw), max(b[3] for b in nw), LY["nwell"])

    # move the drawn lower-left to the origin, add the PR boundary
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
