# domain_mismatch (split)

A signal's `domain` differs between `interface.yaml` and a side spec. One
side thinks it is synchronous to a clock the other side does not use.

**Cheapest fix first:** an analog output with no clock is `clk_free`, and
the digital side must synchronise it - say so in the digital brief.
Correct whichever file disagrees with that.

**Trap:** marking an asynchronous analog output as belonging to the
digital clock domain makes `split` pass and hides a metastability bug that
no gate here checks.
