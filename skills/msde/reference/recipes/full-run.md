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
      state.json            skill msde - split, cosim, top_drc, top_lvs, release
      brief/                the task's spec, verbatim
      interface.yaml        the splitter's crossing signals
      digital_spec.yaml     the digital side's copy of the same entries
      analog_spec.yaml      the analog side's copy
      tb/                   the cosim bench (integrator)
      digital/              nested workspace, skill vde, its own state.json
      analog/               nested workspace, skill ade, its own state.json

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

- **Analog first, all the way.** Its layout (`layout/<block>.gds` and the
  abstract `layout_gen.py` writes beside it) is the digital side's hard
  macro, so the analog side runs through its own layout gates and its own
  release.
- **Digital in parallel, up to synthesis.** P1-P5 need nothing from the
  analog side. Stop once `synth` passes and H1 is recorded; do not start
  its harden, because a harden without the macro is thrown away.

The nested human checkpoints (the analog side's H1/H2, the digital side's
H1) are presented to the person like any other, labelled with the side.

## P3: integrate and the top gates

The integrator writes the macro entry in the digital side's harden config,
the top netlist, and the cosim bench (`recipes/integrate.md`). Then, in
this order:

1. `cosim` - minutes, and it catches a polarity or ratio error before the
   harden spends ten.
2. `state.py edit --class harden_config_edit` in the DIGITAL workspace, then
   its harden as a detached job (`jobs.py start ... --workspace
   {ws}/digital`), polled.
3. The digital side's own P6 signoff gates and its own release, through its
   router's `resume`.
4. `top_drc` and `top_lvs` on the assembled GDS that harden produced.

## P4: release

A fresh reviewer, then `release`. For an msde block it refuses unless every
msde gate is fresh-pass AND both nested workspaces still verify as
released (`check_release.py`, `nested_problems`) - an edit inside either
side after it released reads as `nested_not_released`, and the fix is to
re-release that side, never to waive it. Then `attest.py build`,
`disposition`, and H2.

## Where it stops today

`top_drc` and `top_lvs` are stubs in `gates.yaml` until
`check_top_drc.py`/`check_top_lvs.py` land: both exit 2, nothing is
recorded, and `set-phase P4` refuses. That is the designed behavior; stop
the run there and say so in the P3 digest.
