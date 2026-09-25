# gate_not_ready (release)

`attest.py build()`'s own coverage refusal - some gate this block owes has
no recorded pass, a stale one, or a recorded fail. Not a design defect;
routes to `review`. No script fixes this directly.

**What to do:** read the specific gate named in the finding's message and
its reason (no recorded result / stale: input changed / stale: marked by a
later edit / last recorded result is FAIL). Re-enter that phase: run the
missing gate, re-run the stale one, or resolve the fail through its own
normal fix loop. Then re-attempt `release`.

**The one deliberate exception:** `mc` with no `mc.enabled: true` in
spec.yaml answers "not applicable" and cannot record a result at all
(`SKILL.md`'s Known limits - `check_mc.py`'s applicable:false, `gate.py`
has nothing to key the record on). `release` itself already treats that
case as satisfied; if `gate_not_ready` names a gate OTHER than `mc`, it is
a real gap - never treat it the same way.

**Trap:** a stale mark from an edit class that seemed unrelated (a
`spec_edit`, which marks every ade gate stale) still blocks release
honestly - do not treat "this gate obviously still passes" as a substitute
for actually re-running it.
