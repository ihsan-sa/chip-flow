# Vendored: the Tiny Tapeout GF180 template + precheck

Fetched with git, pinned at these commits (M4, docs/design.md "### M4."):

- `ttgf-verilog-template/` from
  https://github.com/TinyTapeout/ttgf-verilog-template
  @ `e60b92cd8a1e74a1f0cc5e71e113fe2f263a1084` (2026-09-18) - Apache-2.0
  (`LICENSE`). Only `src/config.json` (the fixed LibreLane config every TT
  GF180 project starts from) and `info.yaml` (the pin/tile schema
  `ttlib.py` reads structurally, never typed by hand) are kept; the rest of
  the template (the example RTL, the cocotb test scaffold, CI config) is not
  needed here.

- `ttgf-analog-template/` from
  https://github.com/TinyTapeout/ttgf-analog-template
  @ `5e6eca697b0b84e9a3cd4704a4785fed91ac8a70` (2026-09-15) - Apache-2.0
  (`LICENSE`). Kept: `info.yaml` (the analog project schema: `tiles` 1x2 to
  4x2, `analog_pins` 0 to 6, `uses_vapwr`, the `ua[]` pinout) and
  `src/project.v` (the analog top's port list: `VGND`, `VDPWR`, the digital
  pins and `inout wire [7:0] ua`). Its CI, docs and test scaffold are not
  needed here.

- `tt-support-tools/` from https://github.com/TinyTapeout/tt-support-tools
  @ `01d5d2814fa9dd61e9d211e0b235a4a592a9316a` (main, 2026-08-20) -
  Apache-2.0 (`LICENSE`). Kept: `tech.py` (the per-PDK LibreLane config
  `ttlib.py` imports directly - `tech_map["gf180mcuD"]` - rather than
  copying its numbers by hand), `tech/gf180mcuD/tile_sizes.yaml` (DIE_AREA
  per tile count) and `tech/gf180mcuD/def/tt_block_1x1_pgvdd.def` (the fixed
  IO pin placement template, the "pin constraints come from the template"
  half), and the whole `precheck/` tool (`precheck.py` and its siblings) -
  the signoff check the shuttle itself runs before accepting a submission.
  The analog tile (tt-analog-tile) adds, from the same commit,
  `tech/gf180mcuD/def/analog/tt_analog_{1x2,2x2,3x2,4x2}.def` and their
  `_pgvaa` twins: the analog tiles' pin templates, the digital pins plus
  `ua[7:0]` on Metal4 along the bottom edge. `precheck.py` picks one of
  these whenever `info.yaml` sets `analog_pins > 0`. There is no 1x1 analog
  tile upstream; 1x2 is the smallest.
  The rest of the repo (shuttle assembly, ROM, docs, logo generation) is
  scoped to running an entire tapeout and is not needed to harden and
  precheck one design.

## The one integration point, not a source edit

`precheck.py`'s "Verilog syntax check" runs `yowasp-yosys` (a separate,
WASM-packaged Yosys distribution) as a subprocess found by bare name on
PATH. Vendoring that as a second Yosys, on top of the image's own, would
break "run every tool through bin/eda" (CLAUDE.md) for no reason - the
image already has a real yosys. `check_precheck.py` (engine/scripts/)
prepends a small shim directory to PATH before running precheck.py: a
`yowasp-yosys` script that re-execs `bin/eda yosys` with the same `-p
<script>` invocation precheck.py always uses. `precheck.py` itself is
never edited.

The only other gap: `precheck.py` imports `gdstk`, which the toolchain
image does not carry (klayout, numpy and PyYAML already do).
`engine/lib/ttlib.py`'s `ensure_precheck_deps()` `pip install --target`s it
into a small cache directory (same sandbox-aware cache-root rule as
`bin/eda`'s own shim cache - CLAUDE.md: never write under
`~/.cc/toolchains`) the first time `check_precheck.py` runs, and adds that
directory to `PYTHONPATH` for that one invocation only.
