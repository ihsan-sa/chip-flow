# precheck_failed (precheck)

Tiny Tapeout's own precheck.py flagged a testcase against the hardened top
under `top/`: a wrong top module name, a pin mismatch, or on an analog tile
an analog pad that info.yaml's `analog_pins` claims but the GDS leaves
unwired (or wires without claiming). The finding names the testcase and
precheck's own message.

**Cheapest fix first:** make interface.yaml's `ua_pins` and the analog cell
agree with what the top routes - every pin listed goes to one pad, and the
pads run ua[0] upward with no gap - then `top_harden` again.

**Trap:** precheck reads the ALREADY-hardened top. Fixing interface.yaml or
either side changes nothing until `top_harden` runs again; never edit
`top/` by hand, since `top_harden` rebuilds it.
