# compile_error (lint)

Verilator's `%Error:` with no rule name - a real syntax or elaboration
error, never allowlist-able (`check_lint.py` never even offers the option;
this kind always fails).

**Cheapest fix first:** the message and file:line are verilator's own,
precise - read them before guessing. A missing semicolon, an
undeclared identifier, a width mismatch verilator refuses outright (as
opposed to warns on) are the common causes.

**Trap:** if this fires alongside a wall of unrelated-looking noise, fix
the FIRST reported error and re-run lint before touching anything else -
one real syntax error routinely cascades into dozens of confusing
downstream ones.
