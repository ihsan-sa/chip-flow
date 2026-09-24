* strongarm_comparator.sp - StrongARM latch comparator (docs/design.md 5,
* "### M8."). Section 5's topology library. Not exercised by any M8 corpus
* rung (comparator joins the corpus at M9, "docs/design.md ### M9.", whose
* own sim gates validate a real sizing) - kept here as reference, matching
* section 5's own list ("current mirror, differential pair, two-stage OTA,
* StrongARM comparator, bandgap, R2R ladder, current-starved ring").
*
* TOPOLOGY (classic clocked regenerative comparator, Razavi/Schinkel style):
* while clk is low, Mrp/Mrn precharge the internal drains dp/dn and Mrop/
* Mron precharge the outputs outp/outn, all to vdd, and Mtail is off (no
* static current). When clk rises, Mtail turns on, the input pair (Mip/Min)
* pulls dp/dn apart in proportion to (inp-inn), and the cross-coupled
* latch (Mlp1/Mlp2/Mln1/Mln2) regenerates that small difference to a full
* rail-to-rail digital decision at outp/outn - a comparator with NO static
* power and NO resistor load, decided entirely by clk's edge.
*
*   vdd                                   vdd
*    |--Mrop--outp--Mlp1--|   |--Mlp2--outn--Mron--|
*    |          |          \ /          |          |
*    |         Mln1--------X--------Mln2           |
*    |          |         / \          |            (cross-couple: each
*   Mrp---------dp       inp inn      dn---------Mrn  inverter's gate is
*    |          |                      |          |   the OTHER's drain)
*   vdd        Mip--------tail--------Min         vdd
*               |                      |
*              (gates: inp)          (gates: inn)
*                        \            /
*                         Mtail (gate=clk)
*                              |
*                             vss
*
* DESIGN EQUATIONS:
*   Regeneration time constant tau ~= C_load / gm_latch  [gm_latch = the
*     cross-coupled pair's own transconductance at the decision point -
*     smaller tau means the comparator resolves faster after clk rises]
*   Input-referred offset ~= dominated by Mip/Min threshold MISMATCH
*     (agauss in this PDK's own models) - NOT by the latch devices, since
*     the latch only amplifies whatever imbalance the input pair already
*     produced. Sizing Mip/Min larger reduces mismatch-driven offset at
*     the cost of input capacitance (kT/C-style area-vs-offset tradeoff).
*   Kickback (charge injected back onto inp/inn each clk edge) scales with
*     Mip/Min's own gate-drain overlap capacitance - another reason not to
*     oversize the input pair past what offset needs.
*
* SIZING BOUNDS (nfet_03v3 / pfet_03v3, this PDK):
*   Mtail: W 4e-6 to 2e-5, L 2.8e-7 (minimum) - a wide, minimum-length
*      switch; L is never the design knob here, only W (sets how fast the
*      tail current turns on, trading speed for the input pair's dynamic
*      range at the start of regeneration).
*   Mip/Min (matched pair): W 2e-6 to 2e-5, L 2.8e-7 to 8e-7 - the
*      offset-vs-kickback tradeoff above; longer L helps matching a little
*      at real cost to input pair transconductance (slower initial
*      imbalance).
*   Cross-coupled latch (Mlp1/Mlp2/Mln1/Mln2, matched in pairs): W 2e-6 to
*      1e-5, L 2.8e-7 to 5e-7 - sets gm_latch (regeneration speed); the
*      nfet/pfet pair at each node should be sized so their currents
*      roughly balance at the trip point (pfet W somewhat larger than nfet
*      W, per this PDK's own mobility ratio) or the latch trips asymmetric.
*   Reset devices (Mrp/Mrn/Mrop/Mron, pfet): W 2e-6 to 8e-6, L 2.8e-7 -
*      only need to fully precharge their node before the next clk edge;
*      oversizing wastes area and adds clk load with no speed benefit past
*      that point.
*
* PARAMETERS:
*   w_tail, l_tail           Mtail
*   w_in, l_in               Mip/Min (matched)
*   w_latch_n, w_latch_p, l_latch   Mln1/Mln2 (w_latch_n) and Mlp1/Mlp2
*                            (w_latch_p), shared L
*   w_reset, l_reset         Mrp/Mrn/Mrop/Mron (all four, matched)

.param w_tail=1e-5 l_tail=2.8e-7
.param w_in=8e-6 l_in=2.8e-7
.param w_latch_n=4e-6 w_latch_p=6e-6 l_latch=2.8e-7
.param w_reset=4e-6 l_reset=2.8e-7

.subckt strongarm_comparator inp inn clk outp outn vdd vss
xmtail tail   clk vss vss nfet_03v3 w={w_tail}     l={l_tail}
xmip   dp     inp tail vss nfet_03v3 w={w_in}       l={l_in}
xmin   dn     inn tail vss nfet_03v3 w={w_in}       l={l_in}
xmrp   dp     clk vdd vdd pfet_03v3 w={w_reset}     l={l_reset}
xmrn   dn     clk vdd vdd pfet_03v3 w={w_reset}     l={l_reset}
xmlp1  outp   outn vdd vdd pfet_03v3 w={w_latch_p}  l={l_latch}
xmlp2  outn   outp vdd vdd pfet_03v3 w={w_latch_p}  l={l_latch}
xmln1  outp   outn dp  vss nfet_03v3 w={w_latch_n}  l={l_latch}
xmln2  outn   outp dn  vss nfet_03v3 w={w_latch_n}  l={l_latch}
xmrop  outp   clk vdd vdd pfet_03v3 w={w_reset}     l={l_reset}
xmron  outn   clk vdd vdd pfet_03v3 w={w_reset}     l={l_reset}
.ends strongarm_comparator
