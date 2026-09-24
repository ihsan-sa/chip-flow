* current_mirror.sp - NMOS current mirror (docs/design.md 5, "### M8.").
*
* One of the topology library section 5 names ("current mirror,
* differential pair, two-stage OTA, StrongARM comparator, bandgap, R2R
* ladder, current-starved ring") - a parametrised SPICE template with its
* own design equations and sizing bounds, for the analog-designer agent
* (skills/ade/agents/, M9+) to pick from and instantiate against a real
* spec.yaml `sizing/sizing.yaml`, never used verbatim as a final netlist.
* corpus/ade/mirror is this template scaled to one concrete sizing.
*
* TOPOLOGY: Mref diode-connected (gate tied to drain), mirrors Iref into
* Mout. Both devices share source (vss) and gate (the Mref drain node).
*
*        iref                    iout
*          |                       |
*        [Mref]<-------gate------[Mout]
*          |                       |
*         vss                     vss
*
* DESIGN EQUATIONS (square-law, saturation, gf180's nfet_03v3 is a real
* short-channel device so these are a starting point, not a closed form -
* the sim_pvt/bench_strength gates are what actually validate a sizing):
*   Vgs = Vth + sqrt(2 * Iref / (u_n * Cox * (Wref/Lref)))
*   Iout / Iref = (Wout/Lout) / (Wref/Lref)          [the mirror ratio]
*   Vov (overdrive) = Vgs - Vth                       [sets output headroom:
*                                                       Vds,min ~ Vov]
*   Output impedance ro ~= 1 / (lambda * Iout)        [sets the mirror's
*                                                       own current-source
*                                                       accuracy under a
*                                                       varying load]
*
* SIZING BOUNDS (nfet_03v3, this box's gf180mcuD PDK - see
* libs.tech/ngspice/sm141064.spice's own `.subckt nfet_03v3` default
* w=1e-5 l=2.8e-7 for the process floor):
*   L: 2.8e-7 (process minimum) to 2e-6 - longer L raises ro (better
*      accuracy) at the cost of area and a slower diode-connected pole.
*   W: 1e-6 to 5e-5 - sets Vov at a given Iref; too small raises Vov (eats
*      output headroom), too large wastes area for no accuracy gain once
*      Vov is a few hundred mV.
*   Mirror ratio (Wout/Lout)/(Wref/Lref): 1 to 10 - beyond ~10 the two
*      devices' currents differ enough that mismatch (agauss in this PDK's
*      own models, sw_stat_mismatch=1 by default) dominates the ratio
*      error; a higher ratio wants LONGER L on both devices to hold it.
*   Iref: 1e-6 to 1e-4 A - below 1uA subthreshold effects dominate and the
*      square-law equations above stop being even approximately true.
*
* PARAMETERS (a using netlist overrides these via {{SIZING}}, docs/
* design.md 4 / engine/lib/simlib.py's sizing_param_line):
*   w_ref, l_ref     Mref's W/L
*   w_out, l_out     Mout's W/L (w_out/w_ref sets the mirror ratio when
*                    l_out == l_ref)

.param w_ref=4e-6 l_ref=5e-7 w_out=8e-6 l_out=5e-7

.subckt current_mirror iref_node iout vdd vss
xmref iref_node iref_node vss vss nfet_03v3 w={w_ref} l={l_ref}
xmout iout      iref_node vss vss nfet_03v3 w={w_out} l={l_out}
.ends current_mirror
