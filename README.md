# chip-flow

Claude Code skills for designing chips with open tools: `/vde` for digital blocks in Verilog, `/ade` for analog
blocks, and `/msde` for a mixed-signal task that drives the other two. Each takes a task wherever a project stands,
and every result has to pass gates that are hard to fool: lint, simulation, formal proof, hardening, timing, DRC,
LVS, SPICE at every corner, and planted faults that each gate must catch.

Nothing here is built yet. `docs/design.md` is the plan.

## Checks on GitHub Actions

Every push and pull request runs two jobs in `.github/workflows/checks.yml`, each on a standard runner that unpacks
the same pinned IIC-OSIC-TOOLS image the box uses and runs the tools through `bin/eda`. `tests/check.sh`, the landing
gate, takes about 2 minutes. `tests/check-slow.sh`, the real-tool pytest set, takes about 9 minutes. About 1.5 to 2
minutes of each job goes to pulling the toolchain. To read a run, open it from the Actions tab. The summary page gives
each job's pull time and gate time, and a red job's log ends with the failing check. A failed slow run also keeps its
logs as the `check-slow-logs` artifact for 7 days.
