# pad_missing_from_ua_pins (split)

`analog_spec.yaml` declares a pad under `pads:` that `interface.yaml`'s
`ua_pins` does not map, so `top_harden` would give it no pad at all.

**Cheapest fix first:** add `<pin>: <k>` under `interface.yaml`'s
`ua_pins`, with k the next free pad index (from 0, no gaps). If the pin
is not really a pad, take it out of `pads:`.

**Trap:** do not "fix" this by moving the pin into `signals:`. A pad is
not a crossing signal, and `split` fails that as `pad_in_signals`.
