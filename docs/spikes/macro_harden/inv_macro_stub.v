// inv_macro_stub.v - the blackbox the synthesiser sees for the analog
// inverter macro (gen_inv.py). The layout comes in as GDS + LEF through
// LibreLane's MACROS; this file only declares the ports. Power pins exist
// only under USE_POWER_PINS: the unpowered netlist never names them, and
// PDN_MACRO_CONNECTIONS ties them to VPWR/VGND in the powered one.
(* blackbox *)
module inv_macro (
`ifdef USE_POWER_PINS
    inout  wire vdd,
    inout  wire vss,
`endif
    input  wire in,
    output wire out
);
endmodule
