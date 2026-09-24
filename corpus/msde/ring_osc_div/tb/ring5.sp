* Five-stage RC-lag ring oscillator, ideal threshold-comparator stages (B
* sources) - see ../spec.md for why this stands in for a gf180
* transistor-level ring at M10. Real feedback dynamics through R/C timing,
* none of BSIM's near-zero-leakage/cold-start sensitivity. Staggered .ic
* values (in the bench's extra_lines, not baked in here - the cocotbext-ams
* wrapper netlist always instantiates this subcircuit as "x1", so an
* internal node's .ic must be named "x1.<node>") break the ring's symmetric
* equilibrium so it swings immediately under a cold 'uic' start.
.subckt ring5 osc_out vdd vss
r0 l0 s4 1k
c0 l0 0 5p
b0 s0 0 v='v(l0) < 1.65 ? 3.3 : 0'
r1 l1 s0 1k
c1 l1 0 5p
b1 s1 0 v='v(l1) < 1.65 ? 3.3 : 0'
r2 l2 s1 1k
c2 l2 0 5p
b2 s2 0 v='v(l2) < 1.65 ? 3.3 : 0'
r3 l3 s2 1k
c3 l3 0 5p
b3 s3 0 v='v(l3) < 1.65 ? 3.3 : 0'
r4 l4 s3 1k
c4 l4 0 5p
b4 s4 0 v='v(l4) < 1.65 ? 3.3 : 0'
eout osc_out 0 vol='v(s4)'
.ends ring5
