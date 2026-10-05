// A buffer cell with a specify path, so an SDF has a delay to annotate.
`timescale 1ns/1ps
module a_buf(input A, output Z);
  buf (Z, A);
  specify
    (A => Z) = (0.0, 0.0);
  endspecify
endmodule
