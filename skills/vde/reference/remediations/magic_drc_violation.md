# magic_drc_violation (drc)

magic's own DRC deck found at least one design-rule violation in the
hardened GDS. Routes to `harden`.

**Cheapest fix first:** drc_work's own scratch/report files (ws/log/
drc_work/) carry magic's violation list with layer and location; cross-
check against `klayout_drc_violation` on the same run - the two decks
disagreeing on count is a hint one of them is a known-false-positive rule
class for this PDK rather than a real spacing/width defect.

**Trap:** this is a hardened-GDS-level finding, not something to hand-fix
in the GDS - the fix is a placement/routing setting in harden/config.override.json
(cell padding, routing layer restrictions) or, if the violation traces to
a specific macro/cell choice, the RTL that picks it. Never hand-edit the
GDS.
