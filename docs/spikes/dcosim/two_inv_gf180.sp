* Real gf180mcuD transistor-level two-inverter chain -- kept for the
* record. Under cocotbext-ams's ngspice shared-library bridge (uic-started
* transient, external sources) this aborts with:
*   doAnalyses: TRAN: Timestep too small ... trouble with node "v_vss#branch"
* around t=5-10ns, before the first digital transition even happens, and
* is insensitive to gmin/itl4 stepping, RC input damping, correct .ic
* values on the internal node, and the bridge's sync interval. Looks like
* a real ngspice convergence issue with the uic cold-start this bridge
* uses on active devices, not a netlist mistake -- left as an M10 task.
.subckt two_inv in_pin out_pin vdd vss
xm1 mid    in_pin vss vss nfet_03v3 w=1e-6 l=0.28e-6
xm2 mid    in_pin vdd vdd pfet_03v3 w=2e-6 l=0.28e-6
xm3 out_pin mid   vss vss nfet_03v3 w=1e-6 l=0.28e-6
xm4 out_pin mid   vdd vdd pfet_03v3 w=2e-6 l=0.28e-6
cmid mid 0 1f
cout out_pin 0 1f
.ends two_inv
