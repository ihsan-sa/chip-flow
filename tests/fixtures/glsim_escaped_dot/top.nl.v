// A flattened netlist with a clock-tree buffer on the net `u.clk`, so
// the buffer's instance name is an escaped identifier holding a dot.
`timescale 1ns/1ps
module top(input a, output z);
  wire \u.clk ;
  wire clkbuf_u__clk;  // taken: the plain name the rename would pick first
  a_buf _1_ (.A(a), .Z(\u.clk ));
  a_buf \clkbuf_u.clk  (.A(\u.clk ), .Z(z));
endmodule
