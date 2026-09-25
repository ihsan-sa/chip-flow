# slew_or_cap_or_fanout_violation (timing)

OpenSTA flagged a net over its slew, capacitance or fanout limit - a
signal-integrity/drive-strength concern LibreLane's own sizing/buffering
did not resolve. Routes to `harden`.

**Cheapest fix first:** re-run harden; this class often self-resolves
across LibreLane's own buffer-insertion passes on a rerun. If it
persists, the finding names the net and corner - a `must_keep` cell or an
unusually high-fanout net in the RTL (a shared reset/enable driving many
loads) is the usual real cause.

**Trap:** do not chase this purely inside harden/config.override.json - a genuinely
high-fanout net is often an RTL structuring problem (split the driver
across a buffer tree in the design, not just at the physical layer).
