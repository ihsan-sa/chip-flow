# no_driver (synth)

yosys found a wire that is used but never driven by anything. Routes to
`rtl`.

**Cheapest fix first:** check for a typo'd signal name (the intended
driver exists under a slightly different name) before assuming an actual
missing assignment - this is the single most common cause.

**Trap:** a wire only driven inside a disabled/dead code path (an
`ifdef` never defined, a parameter-gated branch never taken with this
build's parameters) still reports as undriven here even though it "looks"
assigned somewhere in the source - trace the ACTUAL synthesized paths, not
just a text search.
