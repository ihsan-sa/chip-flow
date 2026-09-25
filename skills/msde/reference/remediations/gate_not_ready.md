# gate_not_ready (release)

`attest.py build()` refused: an msde gate (`split`, `cosim`, `top_harden`,
`top_drc`, `top_lvs`) has no recorded pass, a stale one, or a recorded
fail. Not a design defect; no script fixes it.

**What to do:** read which gate the message names and why, re-run or fix
it through its own loop, then re-attempt `release`. `top_harden` re-runs
as a job (`jobs.py start --gate top_harden --workspace <ws> --skill
msde`), and a fresh `top_harden` stales `top_drc` and `top_lvs`, so run
those after it.

**Trap:** do not re-run only the gate named. A stale `top_harden` means
the GDS `top_drc` and `top_lvs` passed on is gone.
