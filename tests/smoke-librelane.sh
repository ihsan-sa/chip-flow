#!/usr/bin/env bash
# tests/smoke-librelane.sh -- slow, not part of tests/check.sh: hardens the
# same tiny counter LibreLane's own way, on gf180mcuD, with its default
# flow. Run once by hand and paste the result; give it minutes, not
# seconds -- OpenROAD placement/routing genuinely takes a while even on a
# design this small, and more so on a loaded box.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
EDA="$REPO/bin/eda"
T="$("$EDA" --print-toolchain-root)" || exit 1

WORK="$(mktemp -d "${TMPDIR:-/tmp}/chip-flow-librelane.XXXXXX")"
echo "run dir: $WORK"
cd "$WORK" || exit 1

cat > counter.v <<'EOF'
module counter (
    input  wire clk,
    input  wire rst,
    output reg [3:0] count
);
  always @(posedge clk) begin
    if (rst)
      count <= 4'd0;
    else
      count <= count + 4'd1;
  end
endmodule
EOF

cat > config.json <<EOF
{
  "DESIGN_NAME": "counter",
  "VERILOG_FILES": "dir::counter.v",
  "CLOCK_PORT": "clk",
  "CLOCK_PERIOD": 10,
  "PDK": "gf180mcuD",
  "STD_CELL_LIBRARY": "gf180mcu_fd_sc_mcu9t5v0",
  "PDK_ROOT": "$T/foss/pdks",
  "FP_SIZING": "absolute",
  "DIE_AREA": "0 0 200 200"
}
EOF

echo "== librelane run =="
"$EDA" librelane --pdk-root "$T/foss/pdks" --overwrite config.json
rc=$?

echo
echo "== run directory =="
find runs -maxdepth 2 2>/dev/null

echo
echo "== last state_out.json (if any) =="
find runs -name "state_out.json" -exec cat {} \; 2>/dev/null | tail -c 2000

echo
echo "== metrics (DRC/LVS/timing, if reported) =="
find runs -iname "*.rpt" -o -iname "*metrics*" 2>/dev/null | head -20

echo
echo "smoke-librelane.sh: librelane exited $rc, run dir left at $WORK"
