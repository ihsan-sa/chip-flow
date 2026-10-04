# prove - run formal on demand

Runs `formal` alone, without touching `sim`/`mutate`/`cover` - for when
someone specifically wants to know whether a property holds, not a full
P4 pass.

## Reading the result

`check_formal.py` classifies every `check: formal|both` requirement as
one of three things, never a plain pass/fail:

- **proven** - full k-induction proof (smtbmc) AND pdr corroborates. This
  is the only verdict that means "holds for all time."
- **bounded** - no counterexample within the spec's own `formal: {depth}`,
  but induction did not converge. Recorded, never reported as proven -
  read the depth before trusting it for anything beyond "nothing broke in
  the first N cycles."
- **failed** - a real counterexample (smtbmc), OR pdr disagrees with a
  smtbmc pass (`engine_disagreement` - PDR is sound for a safety property,
  so its own counterexample is trusted even when k-induction did not also
  find one).

## Where a failure routes

`property_failed`, `engine_disagreement` and `cover_not_reached` ->
`formal`, the property-writer (`fix_dispatch.DOMAINS["formal"]`). It reads
the counterexample against the spec, never the RTL. If the property
misstates the spec it fixes `formal/*.sv` (never weakening); if the
property is right it leaves it and says so in OPEN, and the orchestrator
re-dispatches the finding to `rtl` (formal caught what sim could not -
`docs/design.md` section 2's argument for formal existing). Routing to
`rtl` first would push the rtl fixer, which cannot edit `formal/`, to bend
correct RTL toward a wrong property.

## Cost

Formal can run long on a bigger block (`docs/design.md` section 3's own
"cost of validity" note) - don't invoke `prove` speculatively on every
edit; let the P4 gate sequence run it once per real change instead.
