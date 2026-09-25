* r2r_ladder.sp - 4-bit R2R ladder DAC (docs/design.md 5, "### M8.").
* Section 5's topology library. corpus/ade/r2r_dac is a simpler 2-bit
* realization of the same idea (self-contained, not a literal instantiation
* of this template - see that rung's own netlist for the worked, sim-tested
* case this template generalizes).
*
* TOPOLOGY: the standard R-2R ladder. Each bit drives its rung node
* through a "2R" leg, a series "R" joins each rung to the next one up, the
* LSB rung is terminated by a "2R" leg to vss, and the MSB rung is vout.
* Every node then looks back into 2R || 2R = R, which is what halves each
* bit's weight. A bit is driven to VDD/0 by an ideal external source in this
* template (a SIMULATION-ONLY model of the real switches; in silicon a
* bit's driver is a standard cell - see THE TWO LIMITS below - and an /msde
* split lists those drivers in spec.yaml's `split_devices`).
* Standard R2R math: each bit contributes VDD * 2^-k to vout (k = 1 at the
* MSB), giving vout = VDD * sum(bit_k * 2^(k-N)) for an N-bit code
* (k = 0 the LSB).
*
*   b3(MSB) --2R-- n3 = vout
*                  R
*   b2      --2R-- n2
*                  R
*   b1      --2R-- n1
*                  R
*   b0(LSB) --2R-- n0 --2R-- vss
*
*   vout's output resistance is R (buffer it, or budget it - see below)
*
* DESIGN EQUATIONS:
*   vout (ideal, unloaded) = vdd * (b3/2 + b2/4 + b1/8 + b0/16)
*      where b_k in {0, 1} is that bit's driven logic level as a fraction
*      of vdd (i.e. bit value 0 or 1 exactly, ideal switches)
*   Resolution (1 LSB) = vdd / 2^N
*   INL/DNL depend ENTIRELY on the R:2R ratio actually realized - a ratio
*     error of x% produces a code-dependent output error on that order,
*     which is exactly what corpus/ade/r2r_dac's optimise.py numeric target
*     (sizing/sizing.yaml) tunes back to spec.
*
* SIZING BOUNDS (ppolyf_u, this PDK's unsalicided p+ poly resistor - see
* libs.tech/ngspice/sm141064.spice's own `.subckt ppolyf_u 1 2 3`, pin 3 the
* body, tied to vss): its resistance is
*   R = rsh * (r_length - 2*r_dl) / (r_width - 2*r_dw)
*   rsh = 350 ohm/sq typical (res_typical), 420 at res_ss, 280 at res_ff;
*   r_dw = 25.5 nm, r_dl = 20 pm, so W 2 um x L 150 um is about 27 kohm
* so the RATIO between two units at the SAME r_width depends only on the
* ratio of their r_length (rsh cancels at every corner). This is why a real
* R2R ladder is built by length, and a 2R leg is best two R units in series
* (identical geometry, so edge effects cancel too). `rm1`, which an earlier
* version of this template used, is metal1 (0.09 ohm/sq): a ladder of it
* has almost no resistance and its ratio is set by contacts, not geometry.
*   r_width: 1e-6 to 4e-6 - wider trades area for better matching (lower
*      relative edge-effect variation between nominally-equal legs).
*   r_length (the "R" leg): 1e-6 to 5e-4 - sets the unit R (about 180 ohm
*      to 180 kohm across the width range), and so the two limits below;
*      the "2R" leg is always 2x this by construction, never sized
*      independently in a correct ladder (corpus/ade/r2r_dac's
*      deliberately-wrong start sets it to 1x instead - the fault the
*      sizing/sizing.yaml optimise loop must correct).
*
* THE TWO LIMITS ON UNIT R (real bit drivers, a loaded output):
*   INL from the drivers: a driver's on-resistance adds to its bit's leg,
*     and its pull-up and pull-down differ (a GF180 buf_20 measures about
*     444 and 163 ohm at tt). Trimming the leg by the mean leaves half the
*     difference, dRon, and the MSB branch then errs by about
*     2^(N-1) * dRon / (2R) LSB, so R > 2^(N-1) * dRon for 0.5 LSB -
*     about 18 kohm for N = 8 with buf_20 drivers.
*   Settling: the ladder's output resistance is R, so into a load R_L, C_L
*     tau = (R + R_L) * C_L and settling to 0.5 LSB takes (N + 1) * ln 2 *
*     tau - about 1.2 us for R = 27 kohm into 100 ohm + 7 pF at N = 8.
*   The two pull in opposite directions; a spec that asks for both a small
*     INL and a fast settle against a large load may have no R that meets
*     both, and that is a spec question for the person, not a sizing one.
*
* PARAMETERS:
*   r_width          shared width of every R and 2R leg
*   r_length         the "R" unit length; every "2R" leg is two units in
*                    series

.param r_width=2e-6 r_length=1.5e-4

.subckt r2r_ladder b0 b1 b2 b3 vout vss r_width={r_width} r_length={r_length}
* 2R legs: two R units in series (identical geometry, so the ratio holds)
xr_b0a b0 m0 vss ppolyf_u r_width={r_width} r_length={r_length}
xr_b0b m0 n0 vss ppolyf_u r_width={r_width} r_length={r_length}
xr_b1a b1 m1 vss ppolyf_u r_width={r_width} r_length={r_length}
xr_b1b m1 n1 vss ppolyf_u r_width={r_width} r_length={r_length}
xr_b2a b2 m2 vss ppolyf_u r_width={r_width} r_length={r_length}
xr_b2b m2 n2 vss ppolyf_u r_width={r_width} r_length={r_length}
xr_b3a b3 m3 vss ppolyf_u r_width={r_width} r_length={r_length}
xr_b3b m3 vout vss ppolyf_u r_width={r_width} r_length={r_length}
xr_ta  n0 mt vss ppolyf_u r_width={r_width} r_length={r_length}
xr_tb  mt vss vss ppolyf_u r_width={r_width} r_length={r_length}
* series R: one unit between rungs
xr_s0  n0 n1 vss ppolyf_u r_width={r_width} r_length={r_length}
xr_s1  n1 n2 vss ppolyf_u r_width={r_width} r_length={r_length}
xr_s2  n2 vout vss ppolyf_u r_width={r_width} r_length={r_length}
.ends r2r_ladder
