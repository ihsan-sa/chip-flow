# full-run - a mixed-signal spec.md to a released tile

The whole pipeline, P0 through P4 (`docs/design.md` 1.4), as one recipe.
An msde run owns very little design work of its own: it splits the task,
drives two nested runs through their own skills, joins what they produce
and checks the join. Almost every gate that runs belongs to a nested
workspace and is recorded there, not here.

## Before you start

Derive a short snake_case block name from the task (`sensor_counted`,
`ring_osc_div`) and pass `--workspace blocks/<name>` to the router. Copy
the spec file(s) the task named into `{ws}/brief/` verbatim. For a corpus
rung, copy `spec.md` only - the rung's own `interface.yaml`,
`digital_spec.yaml`, `analog_spec.yaml` and `tb/` are its reference
answer, and handing them to the splitter or integrator defeats the proof.

The workspace ends up shaped like this:

    blocks/<name>/
      state.json            skill msde - split, cosim, top_harden, top_drc, top_lvs, precheck, release
      brief/                the task's spec, verbatim
      interface.yaml        the splitter's crossing signals
      digital_spec.yaml     the digital side's copy of the same entries
      analog_spec.yaml      the analog side's copy
      tb/                   the cosim bench (integrator)
      digital/              nested workspace, skill vde, its own state.json
      analog/               nested workspace, skill ade, its own state.json
      top/                  the assembled chip top, written by top_harden

## P1: split

The splitter writes `interface.yaml`, the two side specs, and one brief per
side (`digital/brief/spec.md`, `analog/brief/spec.md`). `split` must pass
before either nested run starts: a width or direction the two sides
disagree on is a day of wasted work on each side if it is found later.

## P2: the two nested runs

Plan each side with its own router, as the recipe's two `task_router.py`
steps do, then execute that plan under that skill's own playbook
(`skills/ade/SKILL.md`, `skills/vde/SKILL.md`) - its phases, fix loop,
agents, digests and human checkpoints all apply unchanged inside the
nested workspace. Every `--workspace` in those plans already points at
`{ws}/analog` or `{ws}/digital`.

- **Both sides all the way.** Each runs through its own gates to its own
  release. The two need nothing from each other, so run them at once.
- **The digital side hardens alone.** Each crossing signal sits on a spare
  TT pin in its `spec.yaml` `tt_pins` (`osc_out` on `ui_in[7]` in
  `corpus/msde/sensor_counted`), so its own harden, signoff and release
  make a complete standalone tile. `top_harden` later drops those pins and
  wires the signals to the analog macro instead.

Before P3, `attest.py verify` on both nested workspaces must come back
valid.

The nested human checkpoints (each side's H1, the analog H2, the digital
H2) are presented to the person like any other, labelled with the side.

## P3: cosim and the top gates

The integrator checks the two sides' names agree and writes the cosim
bench (`recipes/integrate.md`). Then, in this order:

1. `cosim` - minutes, and it catches a polarity or ratio error before the
   harden spends ten.
2. `top_harden` as a detached job (`jobs.py start --gate top_harden
   --workspace {ws} --skill msde`), polled with `jobs.py status`. It
   assembles `{ws}/top/` from the two sides and hardens it, the analog GDS
   as a LibreLane macro.
3. `top_drc`, `top_lvs` and `precheck` on the GDS it left at
   `top/harden/runs/run/final/gds`. When interface.yaml's `ua_pins` sends
   analog pins to pads, the top is a Tiny Tapeout analog tile and precheck
   checks those pads.

A top gate's finding almost always lives in a side: a DRC error inside the
macro is an analog layout fix, through `analog/`'s own router to a fresh
release, then `top_harden` again.

## P4: release

A fresh reviewer, then `release`. For an msde block it refuses unless every
msde gate is fresh-pass AND both nested workspaces still verify as
released (`check_release.py`, `nested_problems`) - an edit inside either
side after it released reads as `nested_not_released`, and the fix is to
re-release that side, never to waive it. Then `attest.py build`,
`disposition`, and H2.
