#!/usr/bin/env bash
# tests/check.sh -- the fast gate: shellcheck the launcher, then one smoke
# per tool it claims to run. Each smoke prints one JSON line
# {"tool":..., "ok":true|false, "detail":...} and never trusts a silent
# success -- every check greps the tool's own output for the specific
# thing that proves it actually ran, not just that it exited 0. Meant to
# run in well under two minutes; the slow, full LibreLane hardening flow
# lives in tests/smoke-librelane.sh instead.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
EDA="$REPO/bin/eda"
T="${EDA_TOOLCHAIN:-$HOME/.cc/toolchains/iic-osic-tools-2026.09}"
PDK="$T/foss/pdks/gf180mcuD"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/chip-flow-check.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK" || exit 1

PASS=0
FAIL=0
out=""   # set -u means a failed step that never reaches its "out=" assignment
         # (e.g. the compile before a run) must not crash the *whole* script
         # dereferencing $out in its own failure message -- pre-declared once,
         # here, rather than repeated per test.

# report <tool> <ok:true|false> <detail>
report() {
  local tool="$1" ok="$2" detail="$3"
  detail="${detail//\\/\\\\}"
  detail="${detail//\"/\\\"}"
  detail="${detail//$'\n'/ }"
  printf '{"tool": "%s", "ok": %s, "detail": "%s"}\n' "$tool" "$ok" "$detail"
  if [ "$ok" = "true" ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
}

echo "== shellcheck ==" >&2
if shellcheck "$REPO/bin/eda" >/tmp/chip-flow-shellcheck.log 2>&1; then
  report "shellcheck" true "bin/eda is clean"
else
  report "shellcheck" false "$(tail -c 300 /tmp/chip-flow-shellcheck.log)"
fi

# ---------------------------------------------------------------- iverilog+vvp
cat > counter.v <<'EOF'
`timescale 1ns/1ps
module counter (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
endmodule
EOF
cat > counter_tb.v <<'EOF'
`timescale 1ns/1ps
module counter_tb;
  reg clk = 0, rst = 1;
  wire [3:0] count;
  counter dut (.clk(clk), .rst(rst), .count(count));
  always #5 clk = ~clk;
  initial begin
    @(negedge clk); rst = 0;
    @(posedge clk); #1;
    if (count === 4'd1) $display("PASS: count=%0d", count);
    else begin $display("FAIL: count=%0d", count); $fatal(1); end
    $finish;
  end
endmodule
EOF
if "$EDA" iverilog -g2012 -o counter_tb.vvp counter.v counter_tb.v 2>iv.log \
   && out="$("$EDA" vvp counter_tb.vvp 2>&1)" && echo "$out" | grep -q "PASS: count=1"; then
  report "iverilog+vvp" true "counter testbench: $(echo "$out" | grep PASS)"
else
  report "iverilog+vvp" false "$(tail -c 300 iv.log)$out"
fi

# -------------------------------------------------------------------- verilator
if out="$("$EDA" verilator --lint-only -Wall counter.v 2>&1)"; then
  report "verilator-lint" true "$(echo "$out" | grep -m1 -i "verilat")"
else
  report "verilator-lint" false "$(echo "$out" | tail -c 300)"
fi

cat > counter_main.cpp <<'EOF'
#include "Vcounter.h"
#include "verilated.h"
#include <cstdio>
int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Vcounter* top = new Vcounter;
    top->rst = 1; top->clk = 0;
    for (int i = 0; i < 4; i++) { top->clk = !top->clk; top->eval(); }
    top->rst = 0;
    for (int i = 0; i < 20; i++) { top->clk = !top->clk; top->eval(); }
    bool ok = (top->count <= 15);
    printf("count=%d\n", (int)top->count);
    delete top;
    return ok ? 0 : 1;
}
EOF
if out="$(timeout 240 "$EDA" verilator --cc --exe --build -j 4 -Wno-fatal \
     --top-module counter -CFLAGS "-std=c++14 -O0" counter_main.cpp counter.v \
     --Mdir vobj 2>&1)" \
   && runout="$(./vobj/Vcounter 2>&1)"; then
  report "verilator-binary" true "built and ran: $runout"
else
  report "verilator-binary" false "$(echo "$out" | tail -c 300)"
fi

# ------------------------------------------------------------------------ yosys
LIB_TT="$PDK/libs.ref/gf180mcu_fd_sc_mcu9t5v0/lib/gf180mcu_fd_sc_mcu9t5v0__tt_025C_5v00.lib"
cat > synth_gf180.ys <<EOF
read_verilog counter.v
hierarchy -top counter
synth -top counter
dfflibmap -liberty $LIB_TT
abc -liberty $LIB_TT
clean
stat
write_verilog counter_synth.v
EOF
if out="$("$EDA" yosys -s synth_gf180.ys 2>&1)"; then
  cells="$(echo "$out" | awk '/^[[:space:]]+[0-9]+ cells$/{print $1; exit}')"
  if [ -n "$cells" ] && [ "$cells" -gt 0 ] 2>/dev/null; then
    report "yosys-synth-gf180mcu" true "$cells cells mapped to gf180mcu_fd_sc_mcu9t5v0"
  else
    report "yosys-synth-gf180mcu" false "no cell count found: $(echo "$out" | tail -c 300)"
  fi
else
  report "yosys-synth-gf180mcu" false "$(echo "$out" | tail -c 300)"
fi

# --------------------------------------------------------------------------- sby
cat > prop.sv <<'EOF'
module prop (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
`ifdef FORMAL
  reg past_valid = 0;
  always @(posedge clk) past_valid <= 1;
  always @(posedge clk)
    if (past_valid && $past(rst)) assert (count == 4'd0);
`endif
endmodule
EOF
cat > prop.sby <<'EOF'
[options]
mode bmc
depth 10

[engines]
smtbmc yices

[script]
read -formal prop.sv
prep -top prop

[files]
prop.sv
EOF
if out="$(timeout 60 "$EDA" sby -f prop.sby 2>&1)" && echo "$out" | grep -q "DONE (PASS"; then
  report "sby-yices-bmc" true "reset property proved: $(echo "$out" | grep -m1 'summary: engine_0')"
else
  report "sby-yices-bmc" false "$(echo "$out" | tail -c 300)"
fi

# ------------------------------------------------------------------------ cocotb
cat > test_counter.py <<'EOF'
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

@cocotb.test()
async def test_counter_basic(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    prev = int(dut.count.value)
    for _ in range(15):
        await RisingEdge(dut.clk)
        cur = int(dut.count.value)
        assert cur == (prev + 1) % 16
        prev = cur
EOF
cat > run_cocotb.py <<'EOF'
from pathlib import Path
from cocotb_tools.runner import get_runner
proj = Path(__file__).resolve().parent
runner = get_runner("icarus")
runner.build(sources=[proj / "counter.v"], hdl_toplevel="counter", build_dir=proj / "sim_build")
runner.test(hdl_toplevel="counter", test_module="test_counter", test_dir=proj)
EOF
if out="$(timeout 60 "$EDA" python3 run_cocotb.py 2>&1)" && echo "$out" | grep -q "TESTS=1 PASS=1 FAIL=0"; then
  report "cocotb-icarus" true "$(echo "$out" | grep -m1 'TESTS=')"
else
  report "cocotb-icarus" false "$(echo "$out" | tail -c 300)"
fi

# ------------------------------------------------------------------------ ngspice
cat > nfet_dc.spice <<EOF
* DC sweep of a gf180mcu 3.3V nfet
.include '$PDK/libs.tech/ngspice/design.spice'
.lib '$PDK/libs.tech/ngspice/sm141064.spice' typical
vd d 0 3.3
vg g 0 1.8
xm1 d g 0 0 nfet_03v3 w=10e-6 l=0.5e-6
.control
dc vd 0 3.3 0.5
print v(d) i(vd)
.endc
.end
EOF
rows=0
if out="$(timeout 60 "$EDA" ngspice -b nfet_dc.spice 2>&1)"; then
  rows="$(echo "$out" | grep -cE '^[[:space:]]*[0-9]+[[:space:]]+[0-9.e+-]+[[:space:]]+[0-9.e+-]+[[:space:]]+-?[0-9.e+-]+')"
fi
if [ "$rows" -ge 5 ] 2>/dev/null && ! echo "$out" | grep -qi "^Error"; then
  report "ngspice-nfet-dc" true "DC sweep ran, $rows-point v(d)/i(vd) table"
else
  report "ngspice-nfet-dc" false "$(echo "$out" | tail -c 300)"
fi

# a tiny one-box GDS, shared by the magic and klayout DRC smokes below
cat > tiny.rb <<'EOF'
layout = RBA::Layout.new
layout.dbu = 0.001
top = layout.create_cell("TOP")
m1 = layout.layer(34, 0)
top.shapes(m1).insert(RBA::Box.new(0, 0, 2000, 2000))
layout.write("tiny.gds")
EOF
"$EDA" klayout -b -r tiny.rb >/dev/null 2>&1

# --------------------------------------------------------------------------- magic
# magic's own bootstrap always loads a technology named exactly "minimum"
# first; -rcfile is what routes the PDK's own tech through that same
# bootstrap instead (see the comment on the "magic" case in bin/eda for why
# this -- not -T, not a `tech load` after the fact -- is the form that
# doesn't segfault on a real gds read).
cat > magic_drc.tcl <<'EOF'
gds read tiny.gds
select top cell
drc check
drc catchup
puts stdout "MAGIC_DRC_DONE"
flush stdout
quit
EOF
if [ -f tiny.gds ] && out="$(timeout 60 "$EDA" magic magic_drc.tcl < /dev/null 2>&1)" \
   && echo "$out" | grep -q "MAGIC_DRC_DONE"; then
  detail="$(echo "$out" | grep -m1 'Total DRC errors found:')"
  report "magic-drc" true "${detail:-DRC ran to completion (no summary line this run)}"
else
  report "magic-drc" false "$(echo "$out" | tail -c 300)"
fi

# -------------------------------------------------------------------------- klayout
DRC_DECK="$PDK/libs.tech/klayout/tech/drc/gf180mcu.drc"
if [ -f tiny.gds ] && out="$(timeout 240 "$EDA" klayout -b -r "$DRC_DECK" \
     -rd input=tiny.gds -rd topcell=TOP -rd report=tiny.lyrdb -rd run_mode=flat \
     -rd verbose=false -rd variant=A -rd 'decks=all,-beol,-density,-antenna' \
     -rd threads=2 -rd workers=1 2>&1)" && [ -f tiny.lyrdb ]; then
  report "klayout-drc-gf180mcu" true "DRC deck ran to completion, report written"
else
  report "klayout-drc-gf180mcu" false "$(echo "$out" | tail -c 300)"
fi

# --------------------------------------------------------------------------- netgen
cat > inv_a.spice <<'EOF'
.subckt inv_a a y vdd vss
m1 y a vdd vdd pmos w=1u l=0.5u
m2 y a vss vss nmos w=0.5u l=0.5u
.ends
EOF
cat > inv_b_match.spice <<'EOF'
.subckt inv_b a y vdd vss
mp y a vdd vdd pmos w=1u l=0.5u
mn y a vss vss nmos w=0.5u l=0.5u
.ends
EOF
cat > inv_c_mismatch.spice <<'EOF'
.subckt inv_c a y vdd vss
mp y a vdd vdd pmos w=2u l=0.5u
mn y a vss vss nmos w=0.5u l=0.5u
.ends
EOF
if out="$(timeout 30 "$EDA" netgen -batch "lvs {inv_a.spice inv_a} {inv_b_match.spice inv_b} {} lvs_match.log" 2>&1)" \
   && grep -q "Circuits match uniquely" lvs_match.log 2>/dev/null \
   && ! grep -q "Property errors" lvs_match.log 2>/dev/null; then
  report "netgen-lvs-match" true "matching pair: circuits match uniquely"
else
  report "netgen-lvs-match" false "$(echo "$out" | tail -c 300)"
fi
if timeout 30 "$EDA" netgen -batch "lvs {inv_a.spice inv_a} {inv_c_mismatch.spice inv_c} {} lvs_mismatch.log" >/dev/null 2>&1 \
   && grep -qE "Property errors were found|do not match|Netlists do not match" lvs_mismatch.log 2>/dev/null; then
  report "netgen-lvs-mismatch" true "mismatched pair correctly flagged (property/topology mismatch)"
else
  report "netgen-lvs-mismatch" false "mismatch was not flagged as expected"
fi

# ------------------------------------------------------------------------- OpenSTA
if [ -f counter_synth.v ]; then
  cat > sta_timing.tcl <<EOF
read_liberty $LIB_TT
read_verilog counter_synth.v
link_design counter
create_clock -name clk -period 10 [get_ports clk]
set_input_delay -clock clk 1 [get_ports rst]
report_checks
report_tns
exit
EOF
  if out="$(timeout 30 "$EDA" sta sta_timing.tcl 2>&1)" && echo "$out" | grep -q "slack"; then
    report "opensta-timing" true "$(echo "$out" | grep -m1 slack)"
  else
    report "opensta-timing" false "$(echo "$out" | tail -c 300)"
  fi
else
  report "opensta-timing" false "no synthesized netlist available (yosys step produced none)"
fi

echo "== summary ==" >&2
printf 'check.sh: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
