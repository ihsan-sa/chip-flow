# macro_power_unreachable (top_harden)

The analog macro's supply pin (named in the finding) has no Metal3 shape.
The tile's power stripes are vertical Metal4, and they join a macro only
where they cross one of its Metal3 supply shapes, so this pin could never
reach VPWR or VGND. `top_harden` ran no harden; LibreLane would stop at
`openroad-generatepdn` with PDN-0232 and PDN-0233.

**Cheapest fix first:** in `analog/layout/gen_<block>.py`, draw vdd and vss
each as a horizontal Metal3 strap across the cell's full width, joined by
Via2 to the Metal2 or Metal1 supply they already have, with the pin label
on the strap. `corpus/msde/dac_tile/analog/layout/gen_dac_tile_analog.py`
does this. A strap at least one stripe pitch long (38.87 um on GF180) is
sure to cross a stripe. Then run `analog/`'s own layout gates and
`top_harden` again.

**Trap:** keep other Metal3 off the straps' rows, and draw nothing on
Metal4 or above, because the tile's stripes run over the macro.
