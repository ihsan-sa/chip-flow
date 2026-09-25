# flow_step_failed (top_harden)

LibreLane stopped partway through hardening the assembled top (`{ws}/top/`,
the digital side with the analog GDS as a macro). `top_harden` passes on
`check_harden`'s own finding. The digital side already hardened alone, so
the difference is the macro.

**Cheapest fix first:** the finding's message carries the flow's error
tail - read it for the step that failed. A placement or routing failure
around the macro (congested pins at its edge, no room left for the
standard cells) means the macro is too large or its pins sit badly: that
is an analog layout fix, through `analog/`'s own router to a fresh
release, then `top_harden` again. A PDN step failing on the macro's power
pins points at the analog cell's supply and ground pin names.

**Trap:** never edit `top/` - `top_harden` rebuilds it from the two sides
on every run, so the fix always lives in `digital/` or `analog/`.
