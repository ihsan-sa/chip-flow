# yield_all_failed (mc)

Every Monte Carlo sample failed. That is not a yield problem. The nominal
design does not meet its bounds, or the MC deck broke. Routes to `sizing`.

**Cheapest fix first:** confirm `sim_tt` passes on the same inputs. If it
fails, fix that first - MC was never going to pass. If it passes, look at
the MC run's own log for an engine error.

**Trap:** `mc.yield_min: 0` does not make this pass; the gate refuses an
all-failed run at any yield target.
