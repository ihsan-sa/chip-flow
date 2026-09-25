# harden - start the LibreLane job, poll it, never block on it

`harden` is the one gate that runs as a job (`docs/design.md` 1.7) - it
can take the better part of an hour, so `jobs.py start` launches it
detached and returns immediately with a `{pid, log}` record in
`state.jobs`. `gate.py` (running inside the job) records the result
itself when it finishes, exactly as a foreground gate does - there is no
separate "collect the result" step.

## Polling, not blocking

`jobs.py status --workspace <ws> --all` reports `running`, `done`, or
`dead` from the job's own exit-code sidecar (trusted over a live pid,
since a pid can be recycled onto an unrelated process once the real one
has exited). A session that ends mid-job picks it back up on `resume` -
`state.py resume` lists jobs still `running`. A `dead` job restarts with
`jobs.py start`; LibreLane resumes from its own last completed step, not
from scratch.

## Precondition

`synth` must already be fresh-pass. Harden's own recorded inputs are
`rtl`, `harden/config.json`, and `harden/info.yaml` - a synth-clean
netlist does not by itself guarantee harden succeeds (LibreLane's own
floorplan/placement/routing steps can still fail against the TT tile), but
running harden against RTL that has not even synthesized cleanly wastes
the job's own long wall time on a certain failure.
