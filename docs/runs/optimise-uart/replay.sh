#!/bin/bash
# Replays the 12 optimiser changes of the session loop on a fresh corpus UART workspace, then finish.
set -u
# usage: replay.sh [SCRATCH_DIR] - the workspace is SCRATCH_DIR/uart.
R=$(cd "$(dirname "$0")/../../.." && pwd)
ROOT=${1:-$(mktemp -d)}
export U10_WS="$ROOT/uart"; W=$U10_WS
T="$R/bin/eda python $R/docs/runs/optimise-uart/trial.py"
cd "$R" || exit 2; rm -rf "$W"
bin/eda python - <<EOF
import sys, shutil; sys.path.insert(0,'engine/scripts'); sys.path.insert(0,'engine/lib')
import faults
from pathlib import Path
ws=faults.make_scratch_workspace(Path('$ROOT/root'), Path('corpus/vde/uart'), 'vde', 'uart')
shutil.move(str(ws), '$W')
EOF
git -C "$W" init -q && git -C "$W" add -A && git -C "$W" -c user.name=t -c user.email=t@l commit -qm "corpus UART"
bin/eda python engine/scripts/optimise.py start --workspace "$W" --target rtl/uart_tx.v --objective area --trials 12 --wall 90 --patience 6 > "$ROOT/start.json"; echo "start rc=$?"
$T "drop bits_left: end the frame when only the stop bit is left in shift" \
"  localparam FRAME_BITS = 4'd11; // start + 8 data + parity + stop
" "" "  reg [3:0]  bits_left;
" "" "      bits_left <= 4'd0;
" "" "        bits_left <= FRAME_BITS;
" "" "        bits_left <= bits_left - 4'd1;
" "" "        if (bits_left == 4'd1)" "        if (shift[10:1] == 10'd0)"
$T "clk_cnt needs no reset: start clears it before it is read" "      clk_cnt   <= 2'd0;
    end else if" "    end else if"
$T "shift needs no reset: it is loaded on start and only read while busy" "      shift     <= 11'b1_1111_1111_1;
" ""
$T "drive tx from shift[0] combinationally while busy: drops the tx flop and its mux" "output reg        tx," "output wire       tx," "      tx        <= 1'b1;
" "" "      tx <= 1'b1;
" "" "      tx <= shift[0];
" "" "  reg [1:0]  clk_cnt;
" "  reg [1:0]  clk_cnt;

  assign tx = ~busy | shift[0];
"
$T "derive busy from shift != 0: shift empties exactly when the stop bit's hold ends, so the busy flop goes" "output reg        busy" "output wire       busy" "  assign tx = ~busy | shift[0];" "  assign busy = |shift;
  assign tx = ~busy | shift[0];" "      busy      <= 1'b0;
      shift     <= 11'b1_1111_1111_1;" "      shift     <= 11'd0;" "        clk_cnt   <= 2'd0;
        busy      <= 1'b1;" "        clk_cnt   <= 2'd0;" "        shift     <= shift >> 1;
        if (shift[10:1] == 10'd0)
          busy <= 1'b0;" "        shift     <= shift >> 1;"
$T "clk_cnt wraps 3->0 on its own: one unconditional increment instead of a clear-or-increment mux" "      if (clk_cnt == BIT_TICKS) begin
        clk_cnt   <= 2'd0;
        shift     <= shift >> 1;
      end else begin
        clk_cnt <= clk_cnt + 2'd1;
      end" "      clk_cnt <= clk_cnt + 2'd1;
      if (clk_cnt == BIT_TICKS)
        shift     <= shift >> 1;"
$T "busy from shift[10:1] only: the stop bit is 1 anyway, so one fewer OR input" "  assign busy = |shift;" "  assign busy = |shift[10:1];"
$T "fold the idle start check into one condition: else if (!busy && start)" "    end else if (!busy) begin
      if (start) begin
        shift     <= {1'b1, ^data, data, 1'b0};
        clk_cnt   <= 2'd0;
      end
    end else begin" "    end else if (!busy && start) begin
      shift     <= {1'b1, ^data, data, 1'b0};
      clk_cnt   <= 2'd0;
    end else if (busy) begin"
$T "no busy guard on the bit-period branch: idle shift is 0, so shifting it and counting are harmless" "    end else if (busy) begin" "    end else begin"
$T "clk_cnt wraps 3->0 by itself: increment unconditionally while busy, shift on 3" "      if (clk_cnt == BIT_TICKS) begin
        clk_cnt   <= 2'd0;
        shift     <= shift >> 1;
      end else begin
        clk_cnt <= clk_cnt + 2'd1;
      end" "      clk_cnt <= clk_cnt + 2'd1;
      if (clk_cnt == BIT_TICKS)
        shift     <= shift >> 1;"
echo "# loosened" >> "$W/tb/test_uart_tx.py"
$T "clk_cnt == 3 as &clk_cnt; this trial also edits tb/, which the loop must revert" "      if (clk_cnt == BIT_TICKS)" "      if (&clk_cnt)"
$T "clk_cnt == 3 as &clk_cnt, and drop the now-unused BIT_TICKS" "      if (clk_cnt == BIT_TICKS)" "      if (&clk_cnt)" "  localparam BIT_TICKS = 2'd3;   // CLKS_PER_BIT - 1
" ""
bin/eda python engine/scripts/optimise.py finish --workspace "$W" > "$ROOT/finish.json"; echo "finish rc=$?"
