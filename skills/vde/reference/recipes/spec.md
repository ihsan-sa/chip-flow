# spec - write or revise spec.yaml/spec.md

A fresh spec-writer pass on a new block, OR a deliberate revision on a
block that already has RTL, tests, and passing gates. The two look the
same in the recipe (spawn spec-writer, run spec_lint) but mean very
different things once the edit is declared.

## Why the cascade is not a bug

`state.py edit --class spec_edit` marks EVERY vde gate stale
(`invalidation.yaml`). That is correct: a requirement's text, its check
kind, a port width, the clock period - any of these can move on a spec
edit, and every later gate reads spec.yaml somewhere (lint/sim/mutate/
holdout read `top`; synth/harden read ports and clock; release reads the
whole thing through every other gate's own freshness). Do not hand-pick "the
gates this particular edit probably affects" - run the full mapped set.

## What this verb does NOT do

It does not re-write `rtl/`, `tb/`, or `formal/` - those stay as they are
until their own gates fail against the new spec and the fix loop
dispatches a work order. A spec revision on a block with existing RTL
should expect a wave of fix-loop cycles behind it, not a clean re-pass.

## On a brand-new block

Prefer `full-run` instead - it sequences P1 spec-writer, P2 architect, and
P3 tb/formal correctly before any RTL exists. This `spec` verb assumes a
workspace that already exists.
