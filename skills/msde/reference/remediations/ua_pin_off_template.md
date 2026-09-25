# ua_pin_off_template (top_harden)

interface.yaml's `ua_pins` sends analog pins to Tiny Tapeout analog pads
the tile cannot take: an index used twice, a gap below the highest pad, or
more pads than the analog template has. `top_harden` ran no harden. The
finding names the problem.

**Cheapest fix first:** number the analog pins ua[0], ua[1], ... with no
gap, one pin per pad, in interface.yaml's `ua_pins`, then `top_harden`
again. Precheck fails any pad below `analog_pins` that has nothing wired to
it, so a gap is never harmless.

**Trap:** a pin in `ua_pins` leaves the chip on a pad; it cannot also be an
interface signal to the digital side. Never hand-edit `top/spec/spec.yaml`;
`top_harden` rewrites it from interface.yaml on every run.
