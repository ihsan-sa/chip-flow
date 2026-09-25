# spec_lint (the schema-shape kinds)

The fallback for every `spec_lint` kind without its own file: `no_top`,
`measure_not_a_mapping`, `measure_no_name`, `measure_dup_name`,
`measure_bad_severity`. Each one says spec.yaml does not have the shape
`speclib.lint_spec_ade` reads (`skills/ade/templates/spec.yaml.template`
is that shape).

No fixer domain owns these. The spec-writer fixes them inside the `spec`
or `full-run` recipe, and fix_dispatch leaves them in `review` on purpose.

**Cheapest fix first:** compare the file against the template field by
field. `top` is the subckt name the netlist will declare, and every
measure needs a unique `name` that matches a `.measure` in a bench.

**Trap:** don't rename a measure to dodge `measure_dup_name` when the two
entries really are one measure at two corner sets. Keep one entry and give
it the corner list.
