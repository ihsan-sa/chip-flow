# split - one interface, two specs that agree with it

`split` is P1 of a full run, and the verb for any later change to what
crosses between the two sides.

## What the splitter writes

- `interface.yaml` - `signals:`, one entry per crossing signal, each with
  `name`, `direction` (`a2d` or `d2a`), `level`, `domain`, `width` and
  `load`. Every field is required; `check_split.py` refuses (exit 2) an
  entry missing one rather than compare nothing with nothing.
- `digital_spec.yaml` and `analog_spec.yaml` - each an `interface:` list
  with the same entries, plus whatever that side's own spec needs.
- `digital/brief/spec.md` and `analog/brief/spec.md` - the brief each
  nested run starts from, naming its side's crossing signals exactly as
  `interface.yaml` does.

`corpus/msde/sensor_counted/` shows the shape.

## The gate

`split` fails on any `signal_missing_from_spec`, `signal_not_declared` or
`<field>_mismatch` finding. The fix is always to make the three files
agree, and the question is which one is wrong: `interface.yaml` is the
authority unless the brief says otherwise.

## Changing the interface later

The router plans `interface_edit`, which marks `split`, `cosim`, `top_drc`,
`top_lvs` and `release` stale in this workspace. `docs/design.md` 1.6 says
it also marks both nested runs' `spec_edit`, and the engine cannot reach a
nested `state.json` on its own - so the recipe declares `spec_edit` in
`{ws}/digital` and `{ws}/analog` itself. Run those steps whenever the
nested workspace exists; skipping them leaves a nested pass that reads
fresh against an interface that no longer holds.

After the cascade, re-drive each side through its own router (`resume`)
until it is fresh again, then the top gates.

## Known gap

`split` hashes only `interface.yaml` (`invalidation.yaml` gate_inputs). An
edit to a side spec alone does not stale a recorded `split` pass, so after
touching either side spec, re-run `split` by hand.
