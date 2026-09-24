# latch (synth)

yosys's synthesized netlist contains a latch cell - the same root cause as
lint's `LATCH` finding (an incompletely-assigned `always @*`/
`always_comb` block), but caught here because it slipped past (or was
introduced after) `lint`. Routes to `rtl`. See `LATCH.md` for the fix;
this is the same defect, confirmed at synthesis time rather than by
static lint.

**Trap:** if this fires WITHOUT a corresponding lint `LATCH` finding, the
combinational block was likely edited after `lint` last ran, or a
different (RTL-level-invisible) construct inferred the latch during
synthesis - re-run `lint` after fixing this to confirm both gates agree.
