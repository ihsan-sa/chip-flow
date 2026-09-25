"""gen_stdbuf.py - one PDK standard cell in an analog layout, the way a
block's switch drivers are (tests/test_check_analog_lvs.py): a
gf180mcu_fd_sc_mcu7t5v0 buf_1 with a filltie abutted on its right, which
ties the row's nwell to VDD and its substrate to VSS. finalize() flattens
the buffer into its transistors, so LVS has to compare it device by device.
The pin labels sit on the buffer's own metal1 pin labels (its GDS)."""
from __future__ import annotations

import sys

import layoutlib

LIB = "gf180mcu_fd_sc_mcu7t5v0"
CELL = "stdbuf"
BUF_WIDTH = 3.36  # buf_1's placement width (its bbox less the 0.43 nwell)
L1LBL = layoutlib.GF180_LAYER["metal1_label"]


def std_cell(name):
    import gdsfactory as gf

    gds = layoutlib.pdk_root() / "libs.ref" / LIB / "gds" / f"{LIB}.gds"
    return gf.import_gds(gds, cellname=f"{LIB}__{name}")


def generate():
    import gdsfactory as gf

    layoutlib.gf180_cells()
    top = gf.Component()
    top.add_ref(std_cell("buf_1"))
    tie = top.add_ref(std_cell("filltie"))
    tie.dmove((BUF_WIDTH, 0))
    labels = [("I", 0.84, 1.4, L1LBL), ("Z", 2.52, 1.4, L1LBL),
              ("VDD", 1.68, 3.92, L1LBL), ("VSS", 1.68, 0.0, L1LBL)]
    return layoutlib.finalize(top, CELL, labels)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else f"{CELL}.gds"
    generate().write_gds(out)
