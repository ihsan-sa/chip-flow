# LATCH (lint)

Verilator's static check: an `always @*` (or `always_comb`) block with a
path that does not assign every output on every branch infers a latch,
even when nothing downstream reads that path in a way you expect to
matter.

**False positive class:** none, really - a genuine intentional latch is
rare enough in this flow (GF180 synthesis, `synth`'s own `latch` finding
will fail it too) that allowlisting this rule is almost never the right
call. If you truly need one, the allowlist entry's `reason` has to say
why, and `synth`'s post-synthesis latch check still has to agree.

**Cheapest fix first:** add an `else` (or a default case in a `case`
statement, or an initial default assignment before the `case`) that
assigns every output on every path through the block. Usually a one-line
fix once you find the missing branch.

**Trap:** a `case` statement missing a `default:` arm looks clean at a
glance but is the single most common source of this finding - check every
`case` in the flagged block first, not just the `if/else` chains.
