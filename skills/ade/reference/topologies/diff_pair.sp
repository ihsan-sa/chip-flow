* diff_pair.sp - NMOS differential pair with resistive loads (docs/
* design.md 5, "### M8."). Section 5's topology library - a parametrised
* SPICE template with its own equations and bounds, for the
* analog-designer agent (M9+) to pick from; the two-stage OTA and StrongARM
* comparator templates both build on this same input stage.
*
* TOPOLOGY: M1/M2 form the pair, sources tied to a tail current sink Itail,
* drains pulled up through R1/R2 to vdd. inp/inn are the gate inputs;
* outp/outn are the drain outputs (single-to-differential or differential
* output, read either single-ended or across outp-outn).
*
*        vdd            vdd
*         |               |
*        R1              R2
*         |               |
*       outp------M1  M2------outn
*         |    \  |  |  /    |
*        inp----+ tail +----inn
*                  |
*                Itail
*                  |
*                 vss
*
* DESIGN EQUATIONS (square-law, starting point only - see
* current_mirror.sp's own note on why):
*   gm = sqrt(2 * u_n * Cox * (W/L) * Itail/2)     [per-device transconductance,
*                                                    Itail/2 per branch at balance]
*   Differential gain (resistive load) Av = gm * R  [R = R1 = R2]
*   Common-mode input range (upper): vdd - |Vov_load| - Vth_n roughly
*   Common-mode input range (lower): Vov_tail + Vth_n roughly
*   Output swing: bounded by R * Itail/2 pulling outp/outn down from vdd -
*     R too large saturates the load before Itail/2 is reached
*
* SIZING BOUNDS (nfet_03v3, this PDK):
*   L: 2.8e-7 to 1e-6 - short L maximizes gm/Itail (speed/power), longer L
*      trades gain-bandwidth for higher intrinsic gain (gm*ro) and better
*      matching (lower input-referred offset from threshold mismatch).
*   W: 2e-6 to 4e-5 per device - sets Vov at the given tail current; must
*      stay well below the tail current's own headroom (Itail needs
*      Vds,tail >= Vov_tail to behave as a current source).
*   Itail: 5e-6 to 2e-4 A - trades power for gm (and thus gain/speed);
*      matches current_mirror.sp's own Iref range when the tail is a
*      mirrored source rather than the ideal one below.
*   R1 = R2: 5e3 to 5e4 ohm - sets gain directly (Av = gm*R) but a value
*      pulling the drain quiescent point below Vov_tail + a device's own
*      Vov clips the output swing; check outp/outn's DC operating point
*      against Itail*R/2 before trusting Av alone.
*
* PARAMETERS:
*   w_in, l_in       M1/M2's W/L (matched pair - one value each, shared)
*   r_load           R1 = R2
*   i_tail           the ideal tail current (a using netlist replaces this
*                    with a mirrored source from current_mirror.sp for a
*                    real design; ideal here keeps this template's own gain
*                    equation exact for a first sizing pass)

.param w_in=8e-6 l_in=5e-7 r_load=2e4 i_tail=4e-5

.subckt diff_pair inp inn outp outn vdd vss
xm1 outp inp tail vss nfet_03v3 w={w_in} l={l_in}
xm2 outn inn tail vss nfet_03v3 w={w_in} l={l_in}
rload1 vdd outp {r_load}
rload2 vdd outn {r_load}
itail tail vss dc {i_tail}
.ends diff_pair
