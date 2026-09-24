# templates

Starter shapes, not generated boilerplate - an agent copies and edits one
of these, never runs a generator over it. Real, working examples live in
`corpus/vde/*` (read those first for house style); these are the bare
skeleton when a corpus rung isn't a close enough match.

- `spec.yaml.template` - the minimum `spec_lint` needs, one requirement of
  each `check` kind.
- `rtl_lint_allow.yaml.template` - an allowlist entry needs a `rule` AND a
  `reason`; an entry missing either is refused by `check_lint.py`, not
  silently dropped.
