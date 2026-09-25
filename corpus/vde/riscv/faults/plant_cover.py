"""Fault: an unreachable state (gates.yaml's cover row) - a "trap recovery"
branch in EXEC guarded by `!legal` after the `!legal` branch above it has
already taken every such case, so no program and no input sequence can ever
reach it. Its lines drag line coverage under the 95% floor."""
from pathlib import Path

OLD = "          end else if (opcode == OP_SYSTEM) begin\n"
NEW = """          end else if (!legal && opcode == OP_SYSTEM) begin
            // unreachable: every !legal word took the branch above
            illegal <= 1'b0;
            pc      <= 5'd0;
            ld_ptr  <= 7'd0;
            state   <= S_IDLE;
          end else if (!legal && opcode == OP_LOAD) begin
            illegal <= 1'b0;
            pc      <= pc_next[4:0];
            state   <= S_FETCH;
          end else if (!legal && opcode == OP_STORE) begin
            illegal <= 1'b0;
            pc      <= pc_next[4:0];
            state   <= S_FETCH;
          end else if (opcode == OP_SYSTEM) begin
"""


def plant(ws: Path) -> None:
    path = ws / "rtl" / "rv_core.v"
    text = path.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise ValueError(f"{path}: expected exactly one {OLD!r}")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
