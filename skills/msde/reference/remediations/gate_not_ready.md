# gate_not_ready (release)

`attest.py build()` refused: an msde gate (`split`, `cosim`, `top_drc`,
`top_lvs`) has no recorded pass, a stale one, or a recorded fail. Not a
design defect; no script fixes it.

**What to do:** read which gate the message names and why, re-run or fix
it through its own loop, then re-attempt `release`.

**Trap:** `top_drc` and `top_lvs` are stubs until `check_top_drc.py` and
`check_top_lvs.py` land, so today they never record a result and this
finding is expected.
