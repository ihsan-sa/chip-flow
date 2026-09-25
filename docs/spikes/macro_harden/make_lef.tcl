# make_lef.tcl - the LEF abstract of inv_macro, written by magic from the
# generated GDS. Run with the GDS in the current directory:
#   eda magic -noconsole -dnull < make_lef.tcl
# Plain `lef write` (no -pinonly, no -hide): each pin carries its whole net's
# metal, so the abstract is exact. -pinonly would shrink every pin to the
# GDS text label's 10nm point, and -hide blocks Metal3 over the pins.
gds read inv_macro.gds
load inv_macro
select top cell
port makeall
port in  use signal
port in  class input
port out use signal
port out class output
port vdd use power
port vdd class inout
port vss use ground
port vss class inout
property LEFclass BLOCK
lef write inv_macro.lef
quit -noprompt
