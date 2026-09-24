# chip-flow

Claude Code skills for designing chips with open tools: `/vde` for digital blocks in Verilog, `/ade` for analog
blocks, and `/msde` for a mixed-signal task that drives the other two. Each takes a task wherever a project stands,
and every result has to pass gates that are hard to fool: lint, simulation, formal proof, hardening, timing, DRC,
LVS, SPICE at every corner, and planted faults that each gate must catch.

Nothing here is built yet. `docs/design.md` is the plan.
