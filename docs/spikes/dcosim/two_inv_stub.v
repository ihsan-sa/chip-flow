`timescale 1ns/1ps
// Digital-side stub for the cocotbext-ams fallback path: cocotb drives
// in_pin and reads out_pin directly (no logic here -- the two-inverter
// computation happens in the SPICE subcircuit, run by ngspice as a
// lock-step shared library).
module two_inv_top (
    input  in_pin,
    output out_pin
);
endmodule
