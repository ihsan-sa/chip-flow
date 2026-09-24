# fix-timing - a timing fix invalidates the hardened netlist, not just the number

`timing` reads OpenSTA's report on the HARDENED netlist, at LibreLane's own
corners. This verb exists because the generic `fix-finding` loop, applied
naively to a `timing` failure, would re-run `timing` again right after the
fix - against a netlist the fix never actually touched. An SDC or
`harden/config.json` edit, or an RTL restructure, only takes effect once
`harden` regenerates the netlist; `fix-timing` re-hardens before
re-checking, `fix-finding` does not know to.

## Which edit class

The fixer's actual domain decides, and you supply it explicitly
(`--arg edit_class=rtl_edit` or `--arg edit_class=harden_config_edit`) -
this verb does not guess:

- The fix was a constraint or floorplan change (`harden/config.json`,
  `harden/info.yaml`, an SDC the config references) -> `harden_config_edit`
  (marks `harden` onward stale).
- The fix was an RTL restructure (retiming, pipelining, a path that needed
  restructuring, not just re-constraining) -> `rtl_edit` (marks
  everything from `lint` onward stale - a bigger cascade, correctly, since
  the design's own text moved).

## Why re-harden is not optional here

Skipping straight to `gate: timing` after either edit would evaluate
`timing` against the OLD hardened netlist - a stale pass (or a stale,
misleading fail) either way. The recipe always re-runs `harden` (job,
polled) before re-checking `timing`, even when the edit class is
`rtl_edit` and RTL's own `lint`/`sim`/`mutate`/`formal`/`cover`/`synth`
gates need their own separate re-run too (the fix loop handles those the
normal way; this verb only owns the harden-then-timing sequence).

## Today (M5)

`harden` and `timing` are both stubs until M4 lands - this verb's steps
are written against the finished design and need no changes once M4
merges, but running it today ends at `jobs.py start`'s own exit 2.
