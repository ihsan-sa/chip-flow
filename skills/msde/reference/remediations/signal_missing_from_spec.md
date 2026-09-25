# signal_missing_from_spec (split)

A signal `interface.yaml` names is absent from one side spec's `interface:`
list (the message says which side). The two sides would be built against
different pin lists.

**False positive class:** none worth assuming - a name typo (`osc_en` vs
`osc_enable`) is the same defect, spelled differently.

**Cheapest fix first:** decide whether the signal really crosses. If it
does, add the entry to the side spec, copied field for field from
`interface.yaml`, and make that side's `brief/spec.md` name it. If it does
not, remove it from `interface.yaml` - which is an `interface_edit`, with
its nested `spec_edit` cascade.

**Trap:** adding the entry to the side spec but not to that side's brief
leaves the nested spec-writer building without the pin; `split` passes and
`cosim` or `top_lvs` finds it much later.
