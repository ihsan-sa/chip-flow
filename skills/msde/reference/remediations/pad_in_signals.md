# pad_in_signals (split)

An `interface.yaml` signal is an analog pad: a `ua_pins` key, one of
`analog_spec.yaml`'s `pads`, or named `ua`, `ua[k]`, `ua_*`. `top_harden`
and `precheck` route pads only from `ua_pins` and join every signal to the
digital side, so a pad left in `signals:` never reaches its pad.

**Cheapest fix first:** delete the entry from `interface.yaml`'s
`signals:` and from both side specs' `interface:` lists, then map it in
`interface.yaml`'s `ua_pins: {<pin>: <k>}` and list it in
`analog_spec.yaml`'s `pads:`. Drop it from the digital brief too; the
analog brief keeps it as a `.subckt` pin. This is an `interface_edit`.

**Trap:** do not rename the signal to dodge the `ua` name check. If the
digital side really does read the pin, the brief has to say so, and then it
is a crossing signal rather than a pad.
