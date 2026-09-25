# optimise - the sizing loop against a frozen evaluator

docs/design.md section 4, analog form. `optimise.py start --target
sizing/sizing.yaml` freezes the evaluator: the benches, their bounds and
the corner set, hashed. Each trial may change `sizing.yaml` only.
`optimise.py numeric` runs scipy differential evolution inside each entry's
own `min`/`max`, and the agent may append trials a sweep can't make. Both
write `optimise/trials.tsv`.

## Before you start

- Every entry in `sizing.yaml` needs a `min` and a `max` the designer chose
  from the topology's own sizing bounds. An entry with no bound is a
  search over everything, which proves nothing.
- `sim_tt` should run clean, even if it fails its bounds. A bench that errors
  (`sim_engine_error_*`) gives the searcher nothing to climb.
- `--objective margin` is the only objective `optimise.py` accepts today.

## After the winner

`sim_tt`, `sim_pvt` and `bench_strength` all run on the winner. A trial
evaluator that ran at typical does not prove the corners. A winner that
only passes because the bounds are wide is `bench_strength`'s catch, and
the fix for that is the bench, not the sizing. Once a layout exists,
`sizing_edit` stales `lvs` and `pex_sim`, and the generator has to follow
the new sizes.

## What this verb cannot fix

A topology that cannot meet the spec at any size. If the numeric search
tops out with a measure still out of bounds at every trial, stop, and say
so at the next checkpoint. Changing the topology is P2 work, not a trial.
