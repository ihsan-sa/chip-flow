# klayout_drc_violation (top_drc)

KLayout's gf180mcu.drc deck found a violation in the assembled top's GDS.
It runs apart from magic's deck (`magic_drc_violation`), and either can
fail alone.

**Cheapest fix first:** the same as `magic_drc_violation` - read the
report under `top/log/drc_work/`, place it against the macro's location,
and send a violation inside the macro to `analog/`'s own router as a
layout fix, to a fresh release, then `top_harden` again.

**Trap:** "magic passed" does not clear this. The two decks check
different rules, and both must pass.
