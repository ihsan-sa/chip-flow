"""gen_comparator.py - layout generator for the ade/comparator corpus rung
(docs/design.md 5, "### M9."; docs/spikes/glayout.md's fallback).

Draws netlist/comparator.cir's `strongarm_comparator`: five NFETs and six
PFETs, all at L=0.28u, pins inp/inn/clk/outp/outn/vdd/vss. The widths are
this generator's own constants, typed in by the layout-writer from
sizing/sizing.yaml; LVS is what checks them against the netlist.

Floorplan: one row of devices, channels vertical, every device's bottom at
y=0. The NFETs sit left, the PFETs right inside one shared nwell with an N+
tap (vdd) at its right end; a P+ substrate tap (vss) sits left of the NFETs.

Routing is metal1 up and down, metal2 across. Every gate leaves by its
bottom pad down into the gate channel below the row; every source and drain
leaves by a metal1 stub up into the diffusion channel above it. Each channel
is a stack of metal2 tracks, one net per track, and a stub lands on its
net's track through a via1. A stub keeps only the outer 0.30um of its pad,
so it clears the unused top gate pad by more than M1.2a's 0.23um.

  diffusion channel  vss tail dp dn outn outp vdd
  gate channel       clk outp outn  (inp and inn end on their own pads)

outp and outn are drains above the row and latch gates below it: two metal1
columns right of the nwell join each one's upper and lower track.
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
PITCH = 3.0       # device to device, x
TAP_SIZE = 1.0
STUB = 0.30       # metal1 stub width
TRACK = 0.40      # metal2 track width, the via1 landing's own size
TRACK_PITCH = 0.80

# (ref, type, W, gate net, source net, drain net), left to right
NFETS = [
    ("xmip", "n", W_IN, "inp", "tail", "dp"),
    ("xmln1", "n", W_LATCH_N, "outp", "dp", "outn"),
    ("xmtail", "n", W_TAIL, "clk", "vss", "tail"),
    ("xmln2", "n", W_LATCH_N, "outn", "dn", "outp"),
    ("xmin", "n", W_IN, "inn", "tail", "dn"),
]
PFETS = [
    ("xmrp", "p", W_RESET, "clk", "vdd", "dp"),
    ("xmlp1", "p", W_LATCH_P, "outp", "vdd", "outn"),
    ("xmron", "p", W_RESET, "clk", "vdd", "outn"),
    ("xmrop", "p", W_RESET, "clk", "vdd", "outp"),
    ("xmlp2", "p", W_LATCH_P, "outn", "vdd", "outp"),
    ("xmrn", "p", W_RESET, "clk", "vdd", "dn"),
]
PFET_X0 = 15.5    # the first PFET's origin: its nwell clears the NFETs by DF.16
DIFF_NETS = ["vss", "tail", "dp", "dn", "outn", "outp", "vdd"]
GATE_NETS = ["clk", "outp", "outn"]


def shifted(box, dx):
    return (box[0] + dx, box[1], box[2] + dx, box[3])


def generate():
    import gdsfactory as gf

    draw_fet, _draw_res = layoutlib.gf180_cells()
    top = gf.Component()
    labels = []

    placed = []  # (ref, gate, source, drain, pads)
    for i, dev in enumerate(NFETS + PFETS):
        ref, kind, w, g, s, d = dev
        x = i * PITCH if kind == "n" else PFET_X0 + (i - len(NFETS)) * PITCH
        draw = draw_fet.draw_nfet if kind == "n" else draw_fet.draw_pfet
        cell = draw(l_gate=GATE_L, w_gate=w)
        top.add_ref(cell).move((x, 0.0))
        pads = {k: shifted(v, x) for k, v in layoutlib.fet_pads(cell).items()}
        placed.append((ref, g, s, d, pads))

    row_top = max(p["gate_top"][3] for *_, p in placed)
    row_bot = min(p["gate_bot"][1] for *_, p in placed)
    diff_y = {n: row_top + 1.0 + i * TRACK_PITCH for i, n in enumerate(DIFF_NETS)}
    gate_y = {n: row_bot - 1.0 - i * TRACK_PITCH for i, n in enumerate(GATE_NETS)}
    pin_y = row_bot - 1.0 - len(GATE_NETS) * TRACK_PITCH
    diff_x = {n: [] for n in DIFF_NETS}
    gate_x = {n: [] for n in GATE_NETS}

    def stub_up(x0, x1, y_from, net):
        y = diff_y[net]
        layoutlib.rect(top, x0, y_from, x1, y + TRACK / 2, L1)
        cx = round((x0 + x1) / 2, 3)
        layoutlib.via1(top, cx, y)
        diff_x[net].append(cx)

    for ref, g, s, d, p in placed:
        # source and drain: the outer 0.30um of each pad, up to its track
        sp, dp_ = p["s"], p["d"]
        stub_up(sp[0], sp[0] + STUB, sp[3] - 0.2, s)
        stub_up(dp_[2] - STUB, dp_[2], dp_[3] - 0.2, d)
        # gate: the bottom pad, straight down
        gp = p["gate_bot"]
        cx = round((gp[0] + gp[2]) / 2, 3)
        if g in gate_y:
            y = gate_y[g]
            layoutlib.rect(top, gp[0], y - TRACK / 2, gp[2], gp[1] + 0.2, L1)
            layoutlib.via1(top, cx, y)
            gate_x[g].append(cx)
        else:
            layoutlib.rect(top, gp[0], pin_y - 0.5, gp[2], gp[1] + 0.2, L1)
            labels.append((g, cx, pin_y - 0.25, L1LBL))

    # --- vss: a P+ substrate tap left of the NFETs (DF.14), stub up
    tap_x = -3.5
    tap = top.add_ref(layoutlib.psub_tap(TAP_SIZE))
    tap.move((tap_x, 0.0))
    stub_up(tap_x + 0.2, tap_x + 0.8, 0.6, "vss")
    labels.append(("vss", tap_x + 0.5, 0.5, L1LBL))

    # --- vdd: one nwell over every PFET and an N+ tap at its right end
    last_x = PFET_X0 + (len(PFETS) - 1) * PITCH
    ntap_x = last_x + 3.0
    ntap = top.add_ref(layoutlib.nwell_tap(TAP_SIZE))
    ntap.move((ntap_x, 0.0))
    layoutlib.rect(top, PFET_X0 - 1.07, -1.12, ntap_x + TAP_SIZE + 0.6,
                   max(W_LATCH_P, W_RESET) + 1.12, NWELL)
    stub_up(ntap_x + 0.2, ntap_x + 0.8, 0.6, "vdd")
    labels.append(("vdd", ntap_x + 0.5, 0.5, L1LBL))

    # --- outp/outn: a metal1 column each, right of the nwell, joining the
    # net's diffusion track to its gate track
    col_x = {"outp": ntap_x + TAP_SIZE + 2.0, "outn": ntap_x + TAP_SIZE + 3.0}
    for net, cx in col_x.items():
        layoutlib.rect(top, cx - STUB / 2, gate_y[net], cx + STUB / 2,
                       diff_y[net], L1)
        layoutlib.via1(top, cx, diff_y[net])
        layoutlib.via1(top, cx, gate_y[net])
        diff_x[net].append(cx)
        gate_x[net].append(cx)
        labels.append((net, cx, (row_top + row_bot) / 2, L1LBL))

    # --- clk: labelled on its track's leftmost landing
    labels.append(("clk", gate_x["clk"][0], gate_y["clk"], L1LBL))

    # --- the metal2 tracks, each from its first landing to its last
    h = TRACK / 2
    for xs, ys in ((diff_x, diff_y), (gate_x, gate_y)):
        for net, x in xs.items():
            if len(x) > 1:
                layoutlib.rect(top, min(x) - h, ys[net] - h, max(x) + h,
                               ys[net] + h, L2)

    # magic numbers a cell's ports in the order their labels were written,
    # and a bench instantiates the extracted cell positionally: M8's order.
    labels.sort(key=lambda lbl: PINS.index(lbl[0]))
    return layoutlib.finalize(top, CELL, labels)


if __name__ == "__main__":
    comp = generate()
    out_path = sys.argv[1] if len(sys.argv) > 1 else "comparator.gds"
    comp.write_gds(out_path)
    print(f"wrote {out_path}")
