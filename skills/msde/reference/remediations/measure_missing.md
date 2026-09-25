# measure_missing (cosim)

A measure the bounds sidecar names has no value, or a non-numeric one, in
`reports/cosim_measures.json`.

**Cheapest fix first:** the bench computes it too late (after an
assertion that raised), under another name, or not at all. Record every
bounded measure before any assertion, with the sidecar's exact name.

**Trap:** deleting the bound to clear this finding removes a top-level
requirement. The bounds come from the brief.
