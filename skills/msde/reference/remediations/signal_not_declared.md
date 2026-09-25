# signal_not_declared (split)

A side spec lists a crossing signal `interface.yaml` does not. One side
expects a pin the other side was never told about.

**Cheapest fix first:** if the signal truly crosses, add it to
`interface.yaml` with every field and to the other side spec - an
`interface_edit`. If it is internal to that side, delete it from the side
spec's `interface:` list; internal signals do not belong there.

**Trap:** do not "fix" this by copying the side spec's entry into
`interface.yaml` without checking its direction against the brief - the
side that invented the signal may also have guessed which way it flows.
