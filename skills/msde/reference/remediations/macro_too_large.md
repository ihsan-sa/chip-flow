# macro_too_large (top_harden)

The analog macro's LEF is larger than the Tiny Tapeout tile, so
`top_harden` could not place it and ran no harden. The finding gives the
macro's size and the tile's die area.

**Cheapest fix first:** the size comes from the analog layout alone -
`analog/layout/gen_<block>.py` decides it. Make the cell smaller there
(tighter placement, fewer or folded devices within what the sizing
allows), through `analog/`'s own router and its own layout gates to a
fresh release, then `top_harden` again.

**Trap:** fitting the tile is not enough - the standard cells still need
room around the macro to place and route. Never hand-edit the LEF under
`top/macros/`; `top_harden` rewrites it from the GDS on every run.
