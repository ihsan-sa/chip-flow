"""gen_comparator.py - layout generator for the ade/comparator corpus rung
(docs/design.md 5, "### M9."; docs/spikes/glayout.md's fallback).

Draws netlist/comparator.cir's `strongarm_comparator`: five NFETs and six
PFETs, all at L=0.28u, pins inp/inn/clk/outp/outn/vdd/vss. The widths are
this generator's own constants, typed in by the layout-writer from
sizing/sizing.yaml; LVS is what checks them against the netlist.

Two things decide this floorplan, and the post-layout bench measures both.

Symmetry. A StrongARM latch turns any capacitance difference between its
two halves into input offset, and the bench decides on 1 mV. An earlier
draft, which crossed outp and outn over the whole row in channels of
different depth, extracted 0.35 fF more on outn than on outp and decided
the same way whatever the input, up to about 5 mV of it. Here every device
of one half has its twin at the mirrored x in the other (xmip/xmin,
xmln1/xmln2, xmlp1/xmlp2, xmrp/xmrn, xmron/xmrop), pad nets mirrored, and
the one place outp and outn must cross (the latch's cross-coupling) is two
short metal2 tracks each spanning the same x range.

Short outputs. Every fF on outp/outn slows the regeneration the bench's
decision delay measures. Each latch inverter is one column, its NFET under
its PFET: a straight metal1 strip joins their drains (the output) and
another their gates, so the cross-coupling is two tracks between the two
latch columns and nothing of an output leaves the middle of the cell.

  top channel     clk, vdd (metal2)           PFET gate_top pads up to clk
  PFET row        xmrp xmlp1 xmron | xmrop xmlp2 xmrn, one nwell, an N+
                  tap (vdd) at each end; xmlp1-xmron and xmron-xmrop
                  joined in the row (outn, vdd)
  middle channel  the latch columns' drain and gate strips, the dp/dn
                  drain strips, and the outp/outn cross-coupling tracks
  NFET row        xmip xmln1 | xmln2 xmin, top-aligned, dp/dn joined in
                  the row, a P+ tap (vss) at each end
  tail channel    tail (metal2)               input pair sources down to it
  tail row        xmtail, alone on the axis
  bottom channel  clk, vss (metal2)           xmtail's gate and source
  inp and inn run straight down from their gates to pins of their own; a
  metal1 column beyond each end joins the two clk tracks.

A pad stub keeps the outer 0.30um of its pad, so it clears the unused gate
pad by more than M1.2a's 0.23um.
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
PITCH = 2.4       # device centre to centre, x, within a row
FET_CX = 0.33     # a draw_nfet/draw_pfet cell's centre, from its origin
TAP_SIZE = 1.0
STUB = 0.30       # metal1 pad stub width
TRACK = 0.40      # metal2 track width, the via1 landing's own size
TRACK_PITCH = 0.80
GAP = 0.80        # a row's pads to the first track of a channel
GATE_VIA_SHIFT = 0.05  # a latch gate strip's via, off its axis, away from
                       # the drain strip beside it (0.23um to it at zero)
VIA_OUT = 0.10     # a pad stub's via, outward of the stub's centre
PSUB_TAP_DX = 2.67  # a P+ tap's near edge from the outer NFET's centre
NTAP_DX = 2.67      # an N+ tap's near edge from the outer PFET's centre
NWELL_OVER = 0.6    # nwell past the outer edge of an N+ tap
COL_GAP = 1.0       # nwell edge to the clk column

C0, C1, C2 = PITCH / 2, 3 * PITCH / 2, 5 * PITCH / 2
# (ref, W, gate net, left pad net, right pad net, centre x). A device right
# of the axis is its left twin's mirror image, pad nets swapped with it.
NFETS = [
    ("xmip", W_IN, "inp", "tail", "dp", -C2),
    ("xmln1", W_LATCH_N, "outp", "dp", "outn", -C1),
    ("xmln2", W_LATCH_N, "outn", "outp", "dn", C1),
    ("xmin", W_IN, "inn", "dn", "tail", C2),
]
TAIL = ("xmtail", W_TAIL, "clk", "vss", "tail", 0.0)
PFETS = [
    ("xmrp", W_RESET, "clk", "vdd", "dp", -C2),
    ("xmlp1", W_LATCH_P, "outp", "vdd", "outn", -C1),
    ("xmron", W_RESET, "clk", "outn", "vdd", -C0),
    ("xmrop", W_RESET, "clk", "vdd", "outp", C0),
    ("xmlp2", W_LATCH_P, "outn", "outp", "vdd", C1),
    ("xmrn", W_RESET, "clk", "dn", "vdd", C2),
]


def generate():
    import gdsfactory as gf

    draw_fet, _draw_res = layoutlib.gf180_cells()
    top = gf.Component()
    labels = []

    def cell_of(kind, w):
        draw = draw_fet.draw_nfet if kind == "n" else draw_fet.draw_pfet
        return draw(l_gate=GATE_L, w_gate=w)

    def place(dev, kind, y0):
        ref, w, g, left, right, cx = dev
        cell = cell_of(kind, w)
        x0 = round(cx - FET_CX, 3)
        top.add_ref(cell).move((x0, y0))
        pads = {k: (v[0] + x0, v[1] + y0, v[2] + x0, v[3] + y0)
                for k, v in layoutlib.fet_pads(cell).items()}
        return {"ref": ref, "g": g, "left": left, "right": right, "cx": cx,
                "pads": pads}

    def outer(pad, side):
        """The outer STUB-wide strip of a diffusion pad, as (x0, x1)."""
        return (pad[0], pad[0] + STUB) if side == "s" else (pad[2] - STUB, pad[2])

    def stub_via_x(x0, x1, side):
        """A via on a pad stub sits VIA_OUT further out than the stub's
        centre, so its 0.40um landing clears the gate strip beside it."""
        return (x0 + x1) / 2 + (-VIA_OUT if side == "s" else VIA_OUT)

    def vstrip(x0, x1, y0, y1):
        layoutlib.rect(top, x0, min(y0, y1), x1, max(y0, y1), L1)

    # --- rows. The NFET row is top-aligned, so every NFET gate_top pad ends
    # at y=0 and the middle channel is the same depth over every column.
    n_top_rel = {w: layoutlib.fet_pads(cell_of("n", w))["gate_top"][3]
                 for w in {d[1] for d in NFETS}}
    nfets = {d[0]: place(d, "n", -n_top_rel[d[1]]) for d in NFETS}
    n_bot = min(f["pads"]["gate_bot"][1] for f in nfets.values())

    mid_y = {"outn": GAP, "outp": GAP + TRACK_PITCH}
    p_bot_rel = -layoutlib.fet_pads(cell_of("p", W_RESET))["gate_bot"][1]
    p_y0 = round(mid_y["outp"] + GAP + p_bot_rel, 3)
    pfets = {d[0]: place(d, "p", p_y0) for d in PFETS}
    p_top = max(f["pads"]["gate_top"][3] for f in pfets.values())
    top_y = {"vdd": p_top + GAP, "clk": p_top + GAP + TRACK_PITCH}

    tail_y = n_bot - GAP
    t_top_rel = layoutlib.fet_pads(cell_of("n", W_TAIL))["gate_top"][3]
    t_y0 = round(tail_y - GAP - t_top_rel, 3)
    xmtail = place(TAIL, "n", t_y0)
    t_bot = xmtail["pads"]["gate_bot"][1]
    bot_y = {"clk": t_bot - GAP, "vss": t_bot - GAP - TRACK_PITCH}
    pin_y = bot_y["vss"] - GAP

    land = {}  # (channel, net) -> the x of every via on that track
    chan_y = {"mid": mid_y, "top": top_y, "bot": bot_y, "tail": {"tail": tail_y}}

    def via_at(chan, net, x):
        x = round(x, 3)
        layoutlib.via1(top, x, chan_y[chan][net])
        land.setdefault((chan, net), []).append(x)

    # --- middle channel: the columns that join an NFET to the PFET above it
    for (nref, pref) in (("xmip", "xmrp"), ("xmin", "xmrn")):
        n, p = nfets[nref], pfets[pref]
        side = "d" if n["cx"] < 0 else "s"  # the dp/dn pad faces the axis
        a, b = outer(n["pads"][side], side), outer(p["pads"][side], side)
        vstrip(min(a[0], b[0]), max(a[1], b[1]),
               n["pads"][side][3] - 0.2, p["pads"][side][1] + 0.2)
    for (nref, pref) in (("xmln1", "xmlp1"), ("xmln2", "xmlp2")):
        n, p = nfets[nref], pfets[pref]
        # the drain strip: this inverter's own output
        side = "d" if n["cx"] < 0 else "s"
        a, b = outer(n["pads"][side], side), outer(p["pads"][side], side)
        dx0, dx1 = min(a[0], b[0]), max(a[1], b[1])
        vstrip(dx0, dx1, n["pads"][side][3] - 0.2, p["pads"][side][1] + 0.2)
        out_net = n["right"] if n["cx"] < 0 else n["left"]
        via_at("mid", out_net, stub_via_x(dx0, dx1, side))
        labels.append((out_net, (dx0 + dx1) / 2,
                       (mid_y["outn"] + mid_y["outp"]) / 2, L1LBL))
        # the gate strip: the other output, from gate_top up to gate_bot
        gn, gp = n["pads"]["gate_top"], p["pads"]["gate_bot"]
        vstrip(gn[0], gn[2], gn[3] - 0.2, gp[1] + 0.2)
        shift = -GATE_VIA_SHIFT if n["cx"] < 0 else GATE_VIA_SHIFT
        via_at("mid", n["g"], n["cx"] + shift)

    # --- in-row joins: dp/dn in the NFET row, outn/outp and vdd in the PFET row
    def join(a, a_side, b, b_side):
        pa, pb = a["pads"][a_side], b["pads"][b_side]
        y0, y1 = max(pa[1], pb[1]), min(pa[3], pb[3])
        layoutlib.rect(top, min(pa[2], pb[2]) - 0.2, y0,
                       max(pa[0], pb[0]) + 0.2, y1, L1)

    join(nfets["xmip"], "d", nfets["xmln1"], "s")
    join(nfets["xmln2"], "d", nfets["xmin"], "s")
    join(pfets["xmlp1"], "d", pfets["xmron"], "s")
    join(pfets["xmrop"], "d", pfets["xmlp2"], "s")
    join(pfets["xmron"], "d", pfets["xmrop"], "s")

    # --- top channel: vdd from the PFET sources, clk from the reset gates
    for f in pfets.values():
        if f["g"] == "clk":
            gp = f["pads"]["gate_top"]
            vstrip(gp[0], gp[2], gp[3] - 0.2, top_y["clk"] + TRACK / 2)
            via_at("top", "clk", f["cx"])
        for side, net in (("s", f["left"]), ("d", f["right"])):
            if net == "vdd" and f["ref"] not in ("xmron", "xmrop"):
                x0, x1 = outer(f["pads"][side], side)
                vstrip(x0, x1, f["pads"][side][3] - 0.2,
                       top_y["vdd"] + TRACK / 2)
                via_at("top", "vdd", stub_via_x(x0, x1, side))
    # the axis vdd join between xmron and xmrop goes up on the axis itself
    ron = pfets["xmron"]["pads"]["d"]
    vstrip(-STUB / 2, STUB / 2, ron[1] + 0.5, top_y["vdd"] + TRACK / 2)
    via_at("top", "vdd", 0.0)

    # --- tail channel: the input pair's sources and xmtail's drain
    for ref, side in (("xmip", "s"), ("xmin", "d")):
        f = nfets[ref]
        x0, x1 = outer(f["pads"][side], side)
        vstrip(x0, x1, f["pads"][side][1] + 0.2, tail_y - TRACK / 2)
        via_at("tail", "tail", stub_via_x(x0, x1, side))
    td = xmtail["pads"]["d"]
    x0, x1 = outer(td, "d")
    vstrip(x0, x1, td[3] - 0.2, tail_y + TRACK / 2)
    via_at("tail", "tail", stub_via_x(x0, x1, "d"))

    # --- bottom channel: xmtail's gate to clk, its source to vss
    gb = xmtail["pads"]["gate_bot"]
    vstrip(gb[0], gb[2], gb[1] + 0.2, bot_y["clk"] - TRACK / 2)
    via_at("bot", "clk", GATE_VIA_SHIFT)  # away from the vss stub
    ts = xmtail["pads"]["s"]
    x0, x1 = outer(ts, "s")
    vstrip(x0, x1, ts[1] + 0.2, bot_y["vss"] - TRACK / 2)
    via_at("bot", "vss", stub_via_x(x0, x1, "s"))

    # --- inp, inn: straight down from their gate_bot pads to their pins
    for ref in ("xmip", "xmin"):
        f = nfets[ref]
        gp = f["pads"]["gate_bot"]
        vstrip(gp[0], gp[2], gp[1] + 0.2, pin_y - 0.5)
        labels.append((f["g"], f["cx"], pin_y - 0.25, L1LBL))

    # --- vss: a P+ substrate tap at each end of the NFET row (DF.14)
    tap_in = C2 + PSUB_TAP_DX
    tap_y = -n_top_rel[W_IN] + 0.3
    for tap_x in (-tap_in - TAP_SIZE, tap_in):
        tap = top.add_ref(layoutlib.psub_tap(TAP_SIZE))
        tap.move((tap_x, tap_y))
        vstrip(tap_x + 0.2, tap_x + 0.8, tap_y + 0.8, bot_y["vss"] - TRACK / 2)
        via_at("bot", "vss", tap_x + 0.5)
    labels.append(("vss", -tap_in - 0.5, tap_y + 0.5, L1LBL))

    # --- vdd: one nwell over the PFET row, an N+ tap at each end
    ntap_in = C2 + NTAP_DX
    for ntap_x in (-ntap_in - TAP_SIZE, ntap_in):
        ntap = top.add_ref(layoutlib.nwell_tap(TAP_SIZE))
        ntap.move((ntap_x, p_y0))
        vstrip(ntap_x + 0.2, ntap_x + 0.8, p_y0 + 0.2, top_y["vdd"] + TRACK / 2)
        via_at("top", "vdd", ntap_x + 0.5)
    well_x = ntap_in + TAP_SIZE + NWELL_OVER
    layoutlib.rect(top, -well_x, p_y0 - 1.12, well_x,
                   p_y0 + max(W_LATCH_P, W_RESET) + 1.12, NWELL)
    labels.append(("vdd", ntap_in + 0.5, p_y0 + 0.5, L1LBL))

    # --- a clk column beyond each end joins the top and bottom clk tracks
    clk_x = round(max(well_x, tap_in + TAP_SIZE) + COL_GAP, 3)
    for cx in (-clk_x, clk_x):
        vstrip(cx - STUB / 2, cx + STUB / 2, bot_y["clk"], top_y["clk"])
        via_at("bot", "clk", cx)
        via_at("top", "clk", cx)
    labels.append(("clk", -clk_x, (tail_y + mid_y["outn"]) / 2, L1LBL))

    # --- the metal2 tracks, each spanning its own net's landings only
    h = TRACK / 2
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
