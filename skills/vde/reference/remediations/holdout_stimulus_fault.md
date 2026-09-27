# holdout_stimulus_fault (holdout)

A held-out test died in its own stimulus code before any assert judged
the design. The finding names the requirement id(s) and the exception
class, never the test. Held-out tests import the visible `tb/` helpers,
so the usual cause is a helper that changed shape: it returns three
values where the test unpacks two, or it takes a new argument. No RTL is
at fault. Routes to `holdout_stimulus`, which is the tb-writer's.

**Cheapest fix first:** diff the `tb/` helpers the held-out file imports
against the pre-fix snapshot and adapt the held-out call sites to the
new shape. Touch nothing else.

**Never:** change an assert, a `check_*`/`expect_*`/`verify_*` call, a
bound, an expected value, a loop's case list, a decorator, a `# req:`
tag, or a plain helper that computes expected values. Run
`check_holdout_edit.py --workspace <ws> --baseline <pre-fix label>`
before you declare the edit; `state.py edit --class
holdout_stimulus_edit --baseline <label>` refuses the same things.

**Trap:** if the stimulus is right and the design still does the wrong
thing, this is not your finding. Change nothing and say so; the
orchestrator escalates it rather than guessing.
