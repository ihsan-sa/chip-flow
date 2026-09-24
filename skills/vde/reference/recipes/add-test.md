# add-test - one more visible test, scored honestly

Adds a test for a requirement `spec.yaml` already names - never a new
requirement (that is the `spec` verb's job).

## Why tb-writer, fresh context, every time

If the same tb-writer conversation just kept extending its own `tb/`
file across many `add-test` calls, its own earlier choices would start
shaping later ones - exactly the coupling `docs/design.md` section 2
exists to prevent between the tests and the design. Fresh context per
call keeps every test written against the spec alone, not against "what
this suite already looks like."

## Why sim then mutate, in that order

`sim` first: the new test has to pass on the CURRENT rtl before `mutate`
can say anything about it - a test that fails outright is not "the design
is wrong," it might just be a test bug, and `mutate` cannot tell the
difference between those two cases.

`mutate` second: this is the actual point of the verb. A new test that
passes `sim` but still lets a mutant through has not actually added
coverage - it is decorative. A `mutate` failure after `add-test` goes back
to the tb-writer in WORK-ORDER MODE (fresh context, never the generic
`fixer`, never rtl-writer) - re-read `SKILL.md`'s fix loop step 4 and its
`mutate` special case before dispatching this one; `fix_dispatch.py`
already routes every `survivor_*`/`kill_rate_below_threshold` kind, and
every other requirement-coverage kind besides, to the testbench domain via
`cluster_violations.FIXER_HINTS`, so a manual override here is a mistake,
not a judgment call.

`cover` is not run explicitly in this verb's own steps - `tb_edit`'s
mapped gate set includes it, and the router auto-appends it after the
explicit `sim`/`mutate` steps (SKILL.md: "never restate gates in the
verb"). Do not skip it because it "obviously still passes."
