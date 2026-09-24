#!/usr/bin/env bash
# docs/spikes/dcosim/run.sh -- M10 spike: does ngspice's d_cosim/ivlng
# bridge work in this box's image? Section 1 reproduces the answer (no,
# with the exact evidence). Section 2 runs the first fallback from
# design.md section 5 (cocotbext-ams, already in the image) end to end
# and checks the digital side really drove the analog computation. Section
# 3 (M10) resolves the spike's open issue: the same gf180 transistor-level
# two-inverter pair, converging through cocotbext-ams, with a real
# assertion on the digitized readback, not exit 0.
#
# Everything runs through bin/eda; nothing is written under the toolchain.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
EDA="$REPO/bin/eda"
T="$("$EDA" --print-toolchain-root)" || exit 1

WORK="$(mktemp -d "${TMPDIR:-/tmp}/dcosim-spike.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK" || exit 1

echo "== 1. primary path: d_cosim simulation=\"ivlng\" =="

cat > two_inv.v <<'EOF'
`timescale 1ns/1ps
module two_inv (input in, output out);
  wire mid;
  not #(2,2) g1 (mid, in);
  not #(2,2) g2 (out, mid);
endmodule
EOF
"$EDA" iverilog -o two_inv two_inv.v

# The image's own spinit hardcodes /foss/tools/... codemodel paths that
# don't exist once the image is unpacked elsewhere -- point ngspice at a
# corrected copy (same content, this box's real paths) instead of editing
# the read-only tree.
mkdir -p spicehome/scripts
cat > spicehome/scripts/spinit <<EOF
if \$?xspice_enabled
 codemodel $T/foss/tools/ngspice/lib/ngspice/spice2poly.cm
 codemodel $T/foss/tools/ngspice/lib/ngspice/analog.cm
 codemodel $T/foss/tools/ngspice/lib/ngspice/digital.cm
end
EOF

cat > mini.cir <<'EOF'
vin  clk_a 0 dc 0 pulse(0 3.3 10n 1n 1n 48n 100n)
a_in  [%vd(clk_a,0)] [clk_d] adc1
.model adc1 adc_bridge(in_low=1.4 in_high=1.9 rise_delay=1e-9 fall_delay=1e-9)
a_dut [clk_d] [out_d] two_inv
.model two_inv d_cosim simulation="ivlng" sim_args=["two_inv"] delay=0
a_out [out_d] [%vd(out_a,0)] dac1
.model dac1 dac_bridge(out_low=0 out_high=3.3 t_rise=1e-9 t_fall=1e-9)
.control
tran 0.5n 60n
.endc
.end
EOF

out="$(SPICE_LIB_DIR="$WORK/spicehome" "$EDA" ngspice -b mini.cir 2>&1)"
if echo "$out" | grep -q "d_cosim failed to load simulation binary ivlng"; then
  echo "CONFIRMED BROKEN: ivlng.so's own dlopen of libvvp.so is hardcoded to"
  echo "  /foss/tools/ngspice/lib/ngspice/libvvp.so, which does not exist at"
  echo "  that path in this image -- the real file is under iverilog/lib/."
  echo "  (spinit's own /foss codemodel paths are ALSO hardcoded; worked"
  echo "  around above with a corrected local spinit + SPICE_LIB_DIR.)"
  echo "$out" | grep -i "cannot open shared library\|failed to load simulation binary"
else
  echo "UNEXPECTED: ivlng did not fail the way this spike found before."
  echo "$out" | tail -20
fi

echo
echo "== 2. fallback: cocotbext-ams (ngspice shared-library lock-step) =="

cp "$HERE"/two_inv_stub.v "$HERE"/two_inv.sp "$HERE"/test_two_inv.py "$HERE"/run_cocotb.py "$WORK/"

export LD_LIBRARY_PATH="$T/foss/tools/ngspice/lib"
export LIBNGSPICE_PATH="$T/foss/tools/ngspice/lib/libngspice.so.0"
out="$("$EDA" python3 run_cocotb.py 2>&1)"
echo "$out" | grep "cocotb.two_inv_top" # the three in_pin -> out_pin lines
section2_ok=0
if echo "$out" | grep -q "TESTS=1 PASS=1 FAIL=0"; then
  echo "PASS: cocotbext-ams bridge round-tripped the digital pin through a"
  echo "  real ngspice analog computation and back, correctly, 3/3 times."
  section2_ok=1
else
  echo "FAIL: fallback did not pass -- see output below"
  echo "$out" | tail -40
fi

echo
echo "== 3. M10: the gf180 transistor-level pair, through the same bridge =="
echo "   (the open issue this spike left - see two_inv_gf180.sp's own"
echo "   comment and dcosim.md's 'Resolved for M10' section for the fix)"

cp "$HERE"/two_inv_gf180.sp "$HERE"/test_two_inv_gf180.py "$HERE"/run_gf180_cocotb.py "$WORK/"

export GF180_NGSPICE_DIR="$T/foss/pdks/gf180mcuD/libs.tech/ngspice"
out="$("$EDA" python3 run_gf180_cocotb.py 2>&1)"
echo "$out" | grep -E "cocotb\.two_inv_top|Timestep too small"
section3_ok=0
if echo "$out" | grep -q "TESTS=1 PASS=1 FAIL=0"; then
  echo "PASS: the gf180 transistor-level two-inverter pair converged through"
  echo "  cocotbext-ams and the digital side read back a real transition"
  echo "  (asserted, not just exit 0)."
  section3_ok=1
else
  echo "FAIL: the gf180 case did not pass -- see output below"
  echo "$out" | tail -40
fi

echo
if [ "$section2_ok" -eq 1 ] && [ "$section3_ok" -eq 1 ]; then
  echo "ALL PASS"
  exit 0
else
  echo "FAIL: section2_ok=$section2_ok section3_ok=$section3_ok"
  exit 1
fi
