* Ideal rail-to-rail inverter pair (behavioral E-sources): numerically
* well-posed, used to prove the cocotb<->ngspice lock-step bridge itself.
* A real gf180 transistor-level pair (two_inv_gf180.sp, kept for the
* record) does not converge in this harness yet -- see dcosim.md.
.subckt two_inv in_pin out_pin vdd vss
e1 mid     0 vol='v(vdd) - v(in_pin) + v(vss)'
e2 out_pin 0 vol='v(vdd) - v(mid) + v(vss)'
.ends two_inv
