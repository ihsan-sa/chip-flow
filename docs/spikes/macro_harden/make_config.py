"""make_config.py - the LibreLane config.json for the macro_harden spike.

    eda python make_config.py RUN_DIR [--define NAME ...]

RUN_DIR must already hold inv_macro.gds, inv_macro.lef (run.sh makes
both). The base is exactly what the M4 harden gate uses
(engine/lib/ttlib.py::harden_config: the vendored TT template, tile size,
DEF pin template and tech.py's gf180mcuD keys); the spike only adds the
macro keys on top, so what works here drops straight into ttlib.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "engine" / "lib"))
import ttlib  # noqa: E402

TOP = "tt_um_macro_spike"
MACRO = "inv_macro"
INSTANCE = "u_inv"
# Lower-left of the macro in the 346.64 x 160.72 tile, in um. Any spot
# works for the PDN (the Metal3 straps span a full stripe pitch); this one
# leaves the FP_MACRO_*_HALO (10um) clear of the die edge and the top-edge
# TT pins.
LOCATION = [150.0, 50.0]


def pdk_root() -> Path:
    out = subprocess.run([str(REPO / "bin" / "eda"), "--print-toolchain-root"],
                         capture_output=True, text=True, check=True).stdout
    return Path(out.strip()) / "foss" / "pdks"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--define", action="append", default=[])
    args = ap.parse_args()
    run_dir = Path(args.run_dir).resolve()

    spec = {"top": TOP, "clock": {"period_ns": 20}}
    config = ttlib.harden_config(spec, [], HERE / f"{TOP}.v", pdk_root())
    config.update({
        "MACROS": {
            MACRO: {
                "gds": [str(run_dir / f"{MACRO}.gds")],
                "lef": [str(run_dir / f"{MACRO}.lef")],
                "vh": [str(HERE / f"{MACRO}_stub.v")],
                "spice": [str(HERE / f"{MACRO}.spice")],
                "instances": {
                    INSTANCE: {"location": LOCATION, "orientation": "N"},
                },
            },
        },
        # <instance regex> <vdd net> <gnd net> <macro vdd pin> <macro gnd pin>
        "PDN_MACRO_CONNECTIONS": [f"{INSTANCE} VPWR VGND vdd vss"],
        "PDN_CFG": str(HERE / "pdn.tcl"),
        # extract the final layout from GDS, not DEF+LEF, so the macro is
        # extracted to its transistors instead of a LEF blackbox, and give
        # LibreLane's own netgen the macro's subcircuit to compare it with
        "MAGIC_EXT_USE_GDS": True,
        "EXTRA_SPICE_MODELS": [str(HERE / f"{MACRO}.spice")],
    })
    if args.define:
        config["VERILOG_DEFINES"] = list(args.define)
    out = run_dir / "config.json"
    out.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
