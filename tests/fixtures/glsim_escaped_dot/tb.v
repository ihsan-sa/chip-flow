// Raises `a` at 5 ns and prints when `z` follows: 8 ns once the 1 ns
// INTERCONNECT and the 2 ns IOPATH are both annotated.
`timescale 1ns/1ps
module tb;
  reg a = 1'b0;
  wire z;
  top dut(.a(a), .z(z));
  initial begin
    $sdf_annotate(`SDF, dut);
    #5 a = 1'b1;
    #10 $finish;
  end
  always @(posedge z) $display("z rose at %0t", $realtime);
endmodule
