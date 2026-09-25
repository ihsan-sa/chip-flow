# mc_not_a_mapping (spec_lint)

`mc` is present but is not a mapping. `check_mc.py` would refuse it too
(exit 2), never read it as "no Monte Carlo".

**Cheapest fix first:** `mc: {enabled: true, runs: 50, yield_min: 0.95}`
when the brief asks for a yield, or delete the key when it doesn't.

**Trap:** `mc: false` is not a way to switch MC off. Leave the key out.
