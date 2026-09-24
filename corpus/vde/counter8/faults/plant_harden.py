"""Fault: a design too large for the tile (gates.yaml's harden row,
docs/design.md "### M4."). 1024 independent 8-bit counters (~2x
counter8's own ~500-cell footprint... times roughly 1024, not 2x - each
counter is its own 8-bit adder+register, not shared logic) cannot fit the
TT 1x1 tile's ~52000 sq-um core area - LibreLane's own global placement
fails outright, well before routing, so this fails fast rather than
burning a full run.

Each register carries a synthesis-only `(* keep *)` attribute instead of
being folded into the single output port through a reduction tree: an
early version XOR-reduced all N registers into `count`, and the resulting
~1024-input reduction network took yosys/ABC several CPU-minutes to
optimise on its own, before placement ever got a chance to fail - `keep`
gets the same "nothing here is dead code" result (yosys never drops a
kept register) for a fraction of the synthesis cost, since there is no
reduction logic to build or optimise at all."""
from pathlib import Path

N = 1024

LINES = [
    "module counter8 (",
    "    input  wire       clk,",
    "    input  wire       rst,",
    "    output wire [7:0] count",
    ");",
    "  (* keep *) reg [7:0] c [0:{}];".format(N - 1),
    "  integer i;",
    "  always @(posedge clk) begin",
    "    if (rst) begin",
    f"      for (i = 0; i < {N}; i = i + 1) c[i] <= 8'd0;",
    "    end else begin",
    f"      for (i = 0; i < {N}; i = i + 1) c[i] <= c[i] + 8'd1;",
    "    end",
    "  end",
    "  assign count = c[0];",
    "endmodule",
    "",
]
BUGGY = "\n".join(LINES)


def plant(ws: Path) -> None:
    (ws / "rtl" / "counter8.v").write_text(BUGGY, encoding="utf-8")
