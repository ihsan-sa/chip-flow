"""Shared pytest config for the engine suite.

Registers the `slow` marker: a real-tool test (needs the eda image - sby,
yosys, verilator, mcy, cocotb) rather than a Python-side unit test.
CLAUDE.md keeps tests/check.sh under two minutes; tests/check-engine.sh
selects `-m "not slow"` to hold that budget, and tests/check-slow.sh runs
the `slow` set separately under its own, longer hang cap. Registered here
(rather than left implicit) so an unmarked use of `pytest.mark.slow` never
warns as unknown, and `pytest --markers` documents it."""
from __future__ import annotations


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "slow: a real-tool test that needs the eda image - excluded from "
        "tests/check-engine.sh's own run, covered by tests/check-slow.sh",
    )
