## Deliverables

This run is unattended: nobody will answer a question. Where the brief leaves a choice open, pick a sane default, write it down in a comment, and finish the run.

The design is scored after the run ends, from these files only, so leave them in the workspace, either at its root or under `blocks/<name>/`:

- `rtl/`: the design's Verilog (`.v` or `.sv`), with the top module and ports named as in the brief.
- `tb/`: a cocotb testbench, as `tb/test_*.py`.
- `formal/` (optional): any formal properties you wrote.
- If you harden the block: the LibreLane run (LibreLane through `eda`) for the Tiny Tapeout GF180 tile, whose top module is `tt_um_<top>` (`<top>` is the brief's top module), with run tag `run`, so its outputs sit in `harden/runs/run/final/`: `gds/`, `lef/`, `nl/`, `pnl/`, `spice/`, `spef/`, `sdf/`, `sdc/`, `lib/` and `metrics.json`.

Leave real files. A symbolic link is not read.
