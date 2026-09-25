# M10 spike: can LibreLane harden a TT gf180 tile with an analog GDS as a macro?

**Verdict: yes. The digital tile hardens with the analog cell placed as a
LibreLane macro, the final GDS passes magic and KLayout DRC, and netgen
compares it against the powered netlist with the analog cell's own
transistors in it (not a blackbox).**

The scripts in `macro_harden/` are the spike (an inverter macro,
`gen_inv.py`); the recipe below is what `engine/lib/ttlib.py`
(`macro_config`), `engine/scripts/check_top_harden.py` and
`engine/scripts/check_top_lvs.py` took from it. The real run is
`corpus/msde/sensor_counted`: 591 standard cells plus the ring-oscillator
macro in a 1x1 tile, about 17 minutes on a loaded box.

## The recipe

- **Abstract.** magic reads the macro GDS and writes a LEF with a plain
  `lef write` (`make_lef.tcl`). The macro's pins keep their GDS labels.
- **Macro GDS.** A GDS saved by gdsfactory/KLayout carries a
  `$$$CONTEXT_INFO$$$` cell. magic's streamout then sees two top cells and
  `KLayout.Render` fails, so the gate re-saves the GDS with
  `write_context_info=False` first.
- **Config keys** on top of M4's own harden config: `MACROS` (gds, lef, a
  blackbox Verilog stub, the sized `.subckt`, one instance placed in the
  middle of the tile), `PDN_MACRO_CONNECTIONS` for the macro's vdd/vss,
  `MAGIC_EXT_USE_GDS` so extraction sees the macro's transistors, and
  `EXTRA_SPICE_MODELS` for the `.subckt`.
- **Power.** The TT gf180 tile has no Metal5 stripes, and the analog cell's
  supply straps are Metal3, so LibreLane's default macro grid (Metal4 to
  Metal5) never reaches them. `pdn.tcl` (now `engine/lib/macro_pdn.tcl`)
  adds the Metal4-to-Metal3 connect.
- **DRC** is M4's own: magic and KLayout on the final GDS.
- **LVS.** A fresh magic extraction of the final GDS, with the standard
  cells' spice read first so their ports are numbered the way the library
  numbers them, against the powered netlist plus the macro's `.subckt`.
  The gate refuses when the extraction holds fewer transistors outside the
  standard cells than the `.subckt` has, because that means the macro went
  in as a blackbox.
