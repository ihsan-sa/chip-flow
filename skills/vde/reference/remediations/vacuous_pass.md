# vacuous_pass (formal)

smtbmc and pdr both passed an assert, but the cover task, which covers
every assert as well as every cover point, never reached it within the
spec's cover depth (`formal.cover_depth` if set, else `formal.depth`; the
finding names which). No trace ever enables the assert, so its pass
checked nothing. Routes to `formal`.

**Cheapest fix first:** read the assert's enabling condition and every
`assume` in `formal/*.sv` together. An assume that rules out the guarded
state, or assumes that contradict each other, are the usual cause. With
several clocks, check the report's `multiclock` is true: without it every
flop ticks on every step, and an assume that ties one clock to another
cannot hold.

**If the state is reachable but late** (after a long lead-in), raise
`formal.cover_depth`, not `formal.depth`.

**Trap:** don't delete the assert, and don't weaken its guard until it is
trivially enabled. A property that can never fire proves nothing, and one
rewritten so it always fires may no longer say what the requirement says.
