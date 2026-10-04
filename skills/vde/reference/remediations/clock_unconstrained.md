# clock_unconstrained (timing)

OpenSTA reported an infinite (INF) worst slack in the named corner: the
SDC's clock reaches no path in the design at all, not merely a slow one.
Routes to `review` - never `harden`, because the fix is not a
harden-config knob or the RTL, it is the mapping from spec.yaml's
`clock.domains` through `tt_pins` that `engine/lib/ttlib.py`'s
`clock_port()` turns into LibreLane's `CLOCK_PORT`.

**Cheapest fix first:** check spec.yaml's `clock.domains` names the port
the design's flops actually run on, and that `tt_pins` maps that domain to
the pin the block is really clocked from (the template's default is
`clk`; a block clocked from a spare input bit, e.g. an msde divider fed by
a ring oscillator on `ui_in[0]`, needs `clock.domains: [that_name]` and a
matching `tt_pins` entry, or `harden_config()` leaves `CLOCK_PORT` at the
template's `clk` and STA constrains a pin nothing drives). Fix spec.yaml
via `spec_edit`, then re-run `harden` and `timing`.

**Trap:** `CLOCK_PORT` is engine-owned - `harden/config.override.json` may
not set it (harden refuses the override with exit 2), so do not look for a
harden-config fix here; the spec is the only place this is fixed. Do not
"fix" this by adding a `create_clock` for the primary clock to
`harden/constraints.sdc` either - harden refuses a design SDC that
redefines it, because that clock is the spec's. The design SDC carries only
the spec's other clock domains, generated clocks and false paths.
