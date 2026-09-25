"""gen_comparator.py - layout generator for the ade/comparator corpus rung
(docs/design.md 5, "### M9."; docs/spikes/glayout.md's fallback).

Draws netlist/comparator.cir's `strongarm_comparator`: five NFETs and six
PFETs, all at L=0.28u, pins inp/inn/clk/outp/outn/vdd/vss. The widths are
this generator's own constants, typed in by the layout-writer from
sizing/sizing.yaml; LVS is what checks them against the netlist.

Mirror symmetry about x=0 is the point of this floorplan. A StrongARM latch
turns any capacitance or resistance difference between its two halves into
input offset: an earlier one-row draft of this generator, with every net
landing at one end, extracted clean but decided the same way whatever the
input, up to 50mV of it. Here every device of the p side has its twin at
the mirrored x on the n side (xmip/xmin, xmln1/xmln2, xmlp1/xmlp2, xmrp/xmrn,
xmron/xmrop), with its pads' nets mirrored too, so dp/dn and outp/outn see
the same wires. Only xmtail, on the axis, has a left (vss) and right (tail)
pad of its own.

  top channel     outn clk outp      PFET top gate pads up to these
  PFET row        xmrp xmlp1 xmron | xmrop xmlp2 xmrn, in one nwell,
                  an N+ tap (vdd) at each end
  middle channel  vss, tail, dp|dn, outn|outp, vdd (the pairs share a
                  level: dp and outn land left of the axis, dn and outp
                  right of it)
  NFET row        xmip xmln1 xmtail xmln2 xmin, a P+ tap (vss) at each end
  bottom channel  outp clk outn      NFET bottom gate pads down to these;
                  inp and inn go on down to their own pins

Metal1 runs up and down, metal2 across, a via1 where a stub meets its
net's track. A source/drain stub keeps only the outer 0.30um of its pad, so
it clears the unused gate pad by more than M1.2a's 0.23um. Metal1 columns
beyond the taps join each net's tracks across the rows: outp on the right,
outn on the left, and a clk column at each end.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "engine" / "lib"))
import layoutlib  # noqa: E402

L1 = layoutlib.GF180_LAYER["metal1"]
L1LBL = layoutlib.GF180_LAYER["metal1_label"]
L2 = layoutlib.GF180_LAYER["metal2"]
NWELL = layoutlib.GF180_LAYER["nwell"]

CELL = "strongarm_comparator"
PINS = ["inp", "inn", "clk", "outp", "outn", "vdd", "vss"]
GATE_L = 0.28
W_TAIL = 6.0
W_IN = 4.0
W_LATCH_N = 2.0
W_LATCH_P = 4.0
W_RESET = 2.0
PITCH = 4.0       # device centre to centre, x, within a row
FET_CX = 0.33     # a draw_nfet/draw_pfet cell's centre, from its origin
TAP_SIZE = 1.0
STUB = 0.30       # metal1 stub width
TRACK = 0.40      # metal2 track width, the via1 landing's own size
TRACK_PITCH = 0.80
CHANNEL_GAP = 1.0  # a row's pads to the first track of a channel
COL_GAP = 1.0     # the outp/outn column to the clk column beyond it

# (ref, W, gate net, left pad net, right pad net, centre x). A device right
# of the axis is its left twin's mirror image, pad nets swapped with it.
NFETS = [
    ("xmip", W_IN, "inp", "tail", "dp", -2 * PITCH),
    ("xmln1", W_LATCH_N, "outp", "dp", "outn", -PITCH),
    ("xmtail", W_TAIL, "clk", "vss", "tail", 0.0),
    ("xmln2", W_LATCH_N, "outn", "outp", "dn", PITCH),
    ("xmin", W_IN, "inn", "dn", "tail", 2 * PITCH),
]
PFETS = [
    ("xmrp", W_RESET, "clk", "vdd", "dp", -2.5 * PITCH),
    ("xmlp1", W_LATCH_P, "outp", "vdd", "outn", -1.5 * PITCH),
    ("xmron", W_RESET, "clk", "vdd", "outn", -0.5 * PITCH),
    ("xmrop", W_RESET, "clk", "outp", "vdd", 0.5 * PITCH),
    ("xmlp2", W_LATCH_P, "outn", "outp", "vdd", 1.5 * PITCH),
    ("xmrn", W_RESET, "clk", "dn", "vdd", 2.5 * PITCH),
]
# a track level per net; dp/dn and outn/outp share one, left and right
MID_LEVEL = {"vss": 0, "tail": 1, "dp": 2, "dn": 2, "outn": 3, "outp": 3,
             "vdd": 4}
BOT_LEVEL = {"outp": 0, "clk": 1, "outn": 2}   # counted down from the row
TOP_LEVEL = {"outn": 0, "clk": 1, "outp": 2}   # counted up from the row
PSUB_TAP_X = 10.75  # the right P+ tap's left edge; 1.83um clear of xmin
NTAP_DX = 2.67      # an N+ tap's near edge from the outer PFET's centre
NWELL_OVER = 0.6    # nwell past the outer edge of an N+ tap


def generate():
    import gdsfactory as gf

    draw_fet, _draw_res = layoutlib.gf180_cells()
    top = gf.Component()
    labels = []

    def place(dev, kind, y0):
        ref, w, g, left, right, cx = dev
        draw = draw_fet.draw_nfet if kind == "n" else draw_fet.draw_pfet
        cell = draw(l_gate=GATE_L, w_gate=w)
        x0 = round(cx - FET_CX, 3)
        top.add_ref(cell).move((x0, y0))
        pads = {k: (v[0] + x0, v[1] + y0, v[2] + x0, v[3] + y0)
                for k, v in layoutlib.fet_pads(cell).items()}
        return (ref, g, left, right, cx, pads)

    nfets = [place(d, "n", 0.0) for d in NFETS]
    n_top = max(p["gate_top"][3] for *_, p in nfets)
    n_bot = min(p["gate_bot"][1] for *_, p in nfets)
    mid_y = {n: n_top + CHANNEL_GAP + lvl * TRACK_PITCH
             for n, lvl in MID_LEVEL.items()}
    bot_y = {n: n_bot - CHANNEL_GAP - lvl * TRACK_PITCH
             for n, lvl in BOT_LEVEL.items()}
    pin_y = n_bot - CHANNEL_GAP - len(BOT_LEVEL) * TRACK_PITCH
    # the PFET row sits a channel gap above the middle channel's top track
    p_y0 = round(max(mid_y.values()) + CHANNEL_GAP + 0.68, 3)
    pfets = [place(d, "p", p_y0) for d in PFETS]
    p_top = max(p["gate_top"][3] for *_, p in pfets)
    top_y = {n: p_top + CHANNEL_GAP + lvl * TRACK_PITCH
             for n, lvl in TOP_LEVEL.items()}

    # landings per (channel, net): the x of every via on that track
    land = {}

    def via_at(chan, ys, net, cx):
        layoutlib.via1(top, cx, ys[net])
        land.setdefault((chan, net), []).append(cx)

    def pad_stubs(pads, left, right, up):
        """The outer 0.30um of each diffusion pad, to its middle track."""
        for net, box, outer in ((left, pads["s"], pads["s"][0]),
                                (right, pads["d"], pads["d"][2] - STUB)):
            x0, x1 = outer, outer + STUB
            y = mid_y[net]
            if up:
                layoutlib.rect(top, x0, box[3] - 0.2, x1, y + TRACK / 2, L1)
            else:
                layoutlib.rect(top, x0, y - TRACK / 2, x1, box[1] + 0.2, L1)
            via_at("mid", mid_y, net, round((x0 + x1) / 2, 3))

    for ref, g, left, right, cx, p in nfets:
        pad_stubs(p, left, right, up=True)
        gp = p["gate_bot"]
        if g in bot_y:
            y = bot_y[g]
            layoutlib.rect(top, gp[0], y - TRACK / 2, gp[2], gp[1] + 0.2, L1)
            via_at("bot", bot_y, g, cx)
        else:  # inp, inn: straight down to a pin of their own
            layoutlib.rect(top, gp[0], pin_y - 0.5, gp[2], gp[1] + 0.2, L1)
            labels.append((g, cx, pin_y - 0.25, L1LBL))

    for ref, g, left, right, cx, p in pfets:
        pad_stubs(p, left, right, up=False)
        gp = p["gate_top"]
        y = top_y[g]
        layoutlib.rect(top, gp[0], gp[3] - 0.2, gp[2], y + TRACK / 2, L1)
        via_at("top", top_y, g, cx)

    # --- vss: a P+ substrate tap at each end of the NFET row (DF.14)
    for tap_x in (-PSUB_TAP_X - TAP_SIZE, PSUB_TAP_X):
        tap = top.add_ref(layoutlib.psub_tap(TAP_SIZE))
        tap.move((tap_x, 0.0))
        layoutlib.rect(top, tap_x + 0.2, 0.6, tap_x + 0.8,
                       mid_y["vss"] + TRACK / 2, L1)
        via_at("mid", mid_y, "vss", tap_x + 0.5)
    labels.append(("vss", -PSUB_TAP_X - 0.5, 0.5, L1LBL))

    # --- vdd: one nwell over the PFET row, an N+ tap at each end
    outer_cx = PFETS[-1][5]
    ntap_in = outer_cx + NTAP_DX
    for ntap_x in (-ntap_in - TAP_SIZE, ntap_in):
        ntap = top.add_ref(layoutlib.nwell_tap(TAP_SIZE))
        ntap.move((ntap_x, p_y0))
        layoutlib.rect(top, ntap_x + 0.2, mid_y["vdd"] - TRACK / 2,
                       ntap_x + 0.8, p_y0 + 0.6, L1)
        via_at("mid", mid_y, "vdd", ntap_x + 0.5)
    well_x = ntap_in + TAP_SIZE + NWELL_OVER
    layoutlib.rect(top, -well_x, p_y0 - 1.12, well_x,
                   p_y0 + max(W_LATCH_P, W_RESET) + 1.12, NWELL)
    labels.append(("vdd", ntap_in + 0.5, p_y0 + 0.5, L1LBL))

    # --- columns beyond the taps: outp right, outn left, clk at both ends
    col_x = round(well_x + 1.0, 3)
    clk_x = round(col_x + COL_GAP, 3)
    mid_row = (n_top + p_y0) / 2
    for net, cx in (("outp", col_x), ("outn", -col_x)):
        ys = (bot_y[net], mid_y[net], top_y[net])
        layoutlib.rect(top, cx - STUB / 2, min(ys), cx + STUB / 2, max(ys), L1)
        via_at("bot", bot_y, net, cx)
        via_at("mid", mid_y, net, cx)
        via_at("top", top_y, net, cx)
        labels.append((net, cx, mid_row, L1LBL))
    for cx in (-clk_x, clk_x):
        layoutlib.rect(top, cx - STUB / 2, bot_y["clk"], cx + STUB / 2,
                       top_y["clk"], L1)
        via_at("bot", bot_y, "clk", cx)
        via_at("top", top_y, "clk", cx)
    labels.append(("clk", -clk_x, mid_row, L1LBL))

    # --- the metal2 tracks. dp/dn and outn/outp share a level, so a track
    # spans its own net's landings only, never the whole row.
    h = TRACK / 2
    chan_y = {"bot": bot_y, "mid": mid_y, "top": top_y}
    for (chan, net), xs in land.items():
        if len(xs) > 1:
            y = chan_y[chan][net]
            layoutlib.rect(top, min(xs) - h, y - h, max(xs) + h, y + h, L2)

    # magic numbers a cell's ports in the order their labels were written,
    # and a bench instantiates the extracted cell positionally: M8's order.
    labels.sort(key=lambda lbl: PINS.index(lbl[0]))
    return layoutlib.finalize(top, CELL, labels)


if __name__ == "__main__":
    comp = generate()
    out_path = sys.argv[1] if len(sys.argv) > 1 else "comparator.gds"
    comp.write_gds(out_path)
    print(f"wrote {out_path}")
