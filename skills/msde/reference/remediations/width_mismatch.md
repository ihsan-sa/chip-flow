# width_mismatch (split)

A signal's `width` differs between `interface.yaml` and a side spec - the
fault `gates.yaml` names for this gate: "a control word width that differs
between the two". A DAC code or trim word would be truncated or padded.

**Cheapest fix first:** the brief's resolution decides the width. Correct
the disagreeing file; if `interface.yaml` is the wrong one, that is an
`interface_edit` and both nested runs re-spec.

**Trap:** a width change on a side that has already hardened or laid out
is not a text fix - that side's whole run goes stale through the
`spec_edit` cascade, as it should.
