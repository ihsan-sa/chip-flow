* Real gf180mcuD transistor-level two-inverter chain. RESOLVED for M10
* (see dcosim.md's "Resolved for M10" section and run.sh's section 3): the
* original "correct .ic values" attempt above was never actually applied --
* "mid" is an INTERNAL node of the .subckt, and cocotbext-ams's generated
* wrapper netlist always instantiates the user subcircuit as "x1", so the
* real node name at the top level is "x1.mid", not "mid". ngspice silently
* ignores an .ic on a name that does not exist ("IC on non-existent node -
* mid, ignored") rather than erroring, so that fix looked like a no-op.
* With ".ic v(x1.mid)=<expected> v(out_pin)=<expected>" (the AnalogBlock's
* `extra_lines`, run.sh section 3) the SAME netlist converges cleanly
* through cocotbext-ams for a real digital-driven transition -- proven with
* a real assertion on the digitized readback, not exit 0.
*
* A separate, narrower quirk remains and is not this netlist's fault: this
* ngspice build's shared-library transient can still drive its own
* timestep to underflow when FORCED to land exactly on a boundary time (a
* periodic sync fallback, or the analysis's own declared final tstop) --
* reproducible on this exact netlist regardless of .ic, confirmed absent
* under plain batch `ngspice -b` (which never has to land on such a
* boundary) end to end for the same duration. Worked around, not fixed:
* run.sh's bench requests enough duration past its own assertions to have
* margin, and lets the run finish naturally rather than forcing an early
* halt.
.subckt two_inv in_pin out_pin vdd vss
xm1 mid    in_pin vss vss nfet_03v3 w=1e-6 l=0.28e-6
xm2 mid    in_pin vdd vdd pfet_03v3 w=2e-6 l=0.28e-6
xm3 out_pin mid   vss vss nfet_03v3 w=1e-6 l=0.28e-6
xm4 out_pin mid   vdd vdd pfet_03v3 w=2e-6 l=0.28e-6
cmid mid 0 1f
cout out_pin 0 1f
.ends two_inv
