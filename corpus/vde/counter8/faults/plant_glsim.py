"""Fault: glsim's own row (docs/design.md 1.5 "### M4.") names a register
read before it is written, an X-propagation race - genuinely reproducing
one needs a path whose real, SDF-annotated gate delay exceeds the
testbench's own clock period while staying invisible at zero delay; two
attempts at that during this milestone (a linear XOR-rotate chain, then a
200-bit ripple-carry add) were both optimised into a handful of gates by
yosys/ABC and left comfortable timing margin either way - not something
this milestone's time budget could chase further by trial and error.

What this fault demonstrates instead, honestly: the SAME functional bug
plant_sim.py uses for the `sim` gate (docs/design.md "### M2."), reached
through glsim's own pipeline - the gate-level netlist, the generated
tt_pins harness, both cocotb passes - to prove that pipeline catches a
real defect end to end, not only that it can be made to fail somehow. A
genuinely gate-level-only race is future work, noted here rather than
faked."""
from pathlib import Path

BUGGY = """\
module counter8 (
    input  wire       clk,
    input  wire       rst,
    output reg  [7:0] count
);
  always @(posedge clk) begin
    if (rst)
      count <= 8'd0;
    else
      count <= count + 8'd2;
  end
endmodule
"""


def plant(ws: Path) -> None:
    (ws / "rtl" / "counter8.v").write_text(BUGGY, encoding="utf-8")

    import check_harden
    payload, _out = check_harden.run(["--workspace", str(ws)])
    if payload.get("status") != "pass":
        raise RuntimeError(
            f"plant_glsim.py: harden itself did not pass on the buggy "
            f"design (status {payload.get('status')!r}) - the fault needs "
            f"a clean harden output to glsim against: {payload.get('violations')}")
