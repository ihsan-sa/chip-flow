# full-run - spec.md to a released package

The whole pipeline, P1 through P8, as one recipe - the phases ARE the
steps, same as `/hwde`'s. Every phase either writes an artifact through an
agent or checks one through a gate; nothing here is hand-waved.

## Before you start

Derive a short kebab-case block name from the task (`counter8`, `uart_tx`,
`spi_fifo`) and pass `--workspace blocks/<name>` to the router. Copy the
spec file(s) the task named into `{ws}/brief/` verbatim - that is the
WHOLE brief the spec-writer gets, so make sure nothing else rides along
(no reference RTL, no answer key, no other block's files).

## P1-P2: spec and architecture

spec-writer then architect, both fresh context, both re-running
`spec_lint`. If the brief is a corpus rung's `spec.md`, do not also copy
that rung's own `spec.yaml`/`rtl/`/`tb/` into the workspace - those are the
corpus's reference solution and answer key; giving them to the spec-writer
or rtl-writer defeats the whole proof.

## P3: tests before the design exists

tb-writer and property-writer are independent - spawn them in parallel
when the harness supports it, sequentially otherwise. Neither reads the
other's output; both read only `spec/`. `state.py holdout --written-by
tb-writer` pins the held-out set's hash once both are done, BEFORE
rtl-writer ever starts - a later change to `holdout/` becomes visible
against this mark.

## P4: the design, and the fix loop

rtl-writer gets `tb/` and `formal/`, never `holdout/`. Run the six P4
gates in gates.yaml's own order (lint, sim, holdout, mutate, formal,
cover) - on any failure, the fix loop (SKILL.md's own section) takes over
before the next gate runs; do not skip ahead past a failure hoping a later
gate will catch it too. `mutate` and `cover` findings route to the
tb-writer; `holdout` and `sim`/`lint`/`synth` findings route to the
rtl-writer (SKILL.md's fixer-domain table is the full list, and
`cluster_violations.FIXER_HINTS` is where it's enforced).

## H1

Spawn `reviewer` fresh (never the rtl-writer's or any fixer's own
conversation) once P4's gates are all green. Present its digest with the
gate table (numbers first) and the challenge `state.py present
--checkpoint H1` prints to the person; record their reply quoting it with
`state.py human --checkpoint H1 --answer '<reply>'`.

## P5-P6

`synth`, then `harden` as a job (`jobs.py start`, polled with `jobs.py
status` to done/dead, never blocked on synchronously - it can run the
better part of an hour). Once harden finishes, run `timing`, `drc`,
`lvs`, `glsim` and `precheck` in that order; any failure goes through the
fix loop (SKILL.md's own section) before the next gate runs, same as P4.

## P7 (skipped by default)

Optional; invoke the `optimise` verb directly, never as part of a default
full-run.

## P8

`release` (strict - refuses on any gap, by design), `attest.py build` then
`disposition`, H2. Passes once every P6 gate has a fresh recorded pass.
