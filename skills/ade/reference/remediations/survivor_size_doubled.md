# survivor_size_doubled (bench_strength)

Doubling one device's W (or L) left every measure inside its bound and
moved none past its declared `sensitivity`. The bench can't tell this
design from a different one. This is the planted `bench_strength` fault.
Routes to `testbench` (the bench-writer), never the designer.

**Cheapest fix first:** check each bound against the spec. The bench's
`min`/`max` must equal the spec's (spec_lint fails anything else), so a
bound looser than the spec is the fix: copy the spec's value. If the
bounds already match, read the survivor's `deltas` in the gate's facts.
A measure that moved but stayed inside the spec gets a `sensitivity` on
its bound in `tb/*.bounds.json`: the relative move, against the unmutated
design's own tt value, that the bench counts as a kill. Declare
max(3 sigma, 2%) of that measure's mc spread, 2% when no mc sigma is
known. The gate refuses less than 2%, and refuses a sensitivity on a
measure that sits near zero (v_low, an off current), where a relative
move is only simulator tolerance. It is not a pass bound, so the
design's sim_tt and sim_pvt results don't change.

**Trap:** don't resize the design so the mutant happens to fail, and
don't set a sensitivity below the measure's real spread just to catch
this mutant. If no spec measure moves at all, the spec lacks the measure
this device sets: say so under OPEN, because a bench may not score a
measure the spec doesn't declare.
