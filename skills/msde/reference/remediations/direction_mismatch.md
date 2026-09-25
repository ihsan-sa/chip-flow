# direction_mismatch (split)

A signal's `direction` (`a2d` or `d2a`) differs between `interface.yaml`
and a side spec. Both sides would drive the net, or neither would.

**Cheapest fix first:** the brief decides who drives it. Correct the file
that disagrees with the brief - usually the side spec. If `interface.yaml`
itself is wrong, that is an `interface_edit` with the nested cascade.

**Trap:** a bidirectional-looking signal (an enable that is also read
back) is two signals, one per direction. Split it rather than picking one.
