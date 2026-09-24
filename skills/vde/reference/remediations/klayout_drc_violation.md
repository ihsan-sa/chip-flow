# klayout_drc_violation (drc)

KLayout's gf180mcu.drc deck found at least one design-rule violation,
independent of magic's own deck (`magic_drc_violation`) - the two run
separately and either can fail alone. Routes to `harden`.

**Cheapest fix first:** same as `magic_drc_violation` - read drc_work's
own report under ws/log/drc_work/ first, and compare against magic's
count on the same run before assuming it is a real defect vs. a deck-
specific quirk.

**Trap:** do not treat "magic passed" as clearing this - the two decks
check different rule sets and neither substitutes for the other; both
must pass.
