"""The detail-level briefs of a corpus rung (docs/design-evals.md section 2).

Every level states the same requirements and numbers as spec.md, and none
leaks a held-out test name, a formal property name, or a path the session
under test must not see. Add a rung by adding its name to RUNGS.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNGS = {
    "counter8": {"ports": {"clk": 1, "rst": 1, "count": 8}, "period": "20"},
}
LEVELS = ["spec.md", "spec.terse.md", "spec.full.md"]
BANNED = ["holdout", "corpus", "spec.yaml"]


def rung_dir(rung):
    return ROOT / "corpus" / "vde" / rung


def held_out_names(rung):
    names = set()
    for f in (rung_dir(rung) / "holdout").glob("*.py"):
        names |= set(re.findall(r"^\s*(?:async\s+)?def\s+(test_\w+)", f.read_text(), re.M))
    for f in (rung_dir(rung) / "formal").glob("*.sv"):
        names |= set(re.findall(r"^\s*(\w+)\s*:\s*(?:assert|assume|cover)\b", f.read_text(), re.M))
    return names


def checked_numbers(rung):
    # numbers in the Behaviour and Interface sections of the typical brief
    text = (rung_dir(rung) / "spec.md").read_text()
    body = text[text.index("## Behaviour"):]
    return set(re.findall(r"(?<![\w.])\d+(?![\w.])", body))


@pytest.mark.parametrize("rung", sorted(RUNGS))
@pytest.mark.parametrize("level", LEVELS)
def test_brief_states_the_same_numbers_and_ports(rung, level):
    text = (rung_dir(rung) / level).read_text()
    found = set(re.findall(r"(?<![\w.])\d+(?![\w.])", text))
    assert checked_numbers(rung) <= found, checked_numbers(rung) - found
    assert RUNGS[rung]["period"] in found
    for port, width in RUNGS[rung]["ports"].items():
        row = re.search(rf"\|\s*{port}\s*\|[^|]*\|\s*{width}\s*\|", text)
        assert row, f"{level}: no interface row for {port} with width {width}"
    assert re.search(r"synchronous", text) and re.search(r"active-high", text)
    assert re.search(r"255", text) and re.search(r"one clock", text)


@pytest.mark.parametrize("rung", sorted(RUNGS))
@pytest.mark.parametrize("level", LEVELS)
def test_brief_leaks_nothing(rung, level):
    text = (rung_dir(rung) / level).read_text()
    names = held_out_names(rung)
    assert names, "parsed no held-out or property names; the parser is stale"
    for bad in sorted(names) + BANNED:
        assert bad not in text, f"{level} contains {bad!r}"


def tile_statement(rung, level):
    lines = [ln for ln in (rung_dir(rung) / level).read_text().splitlines()
             if f"tt_um_{rung}" in ln]
    return lines[0].lstrip("- ").strip() if len(lines) == 1 else None


@pytest.mark.parametrize("rung", sorted(RUNGS))
@pytest.mark.parametrize("level", LEVELS)
def test_brief_states_the_tile_pin_map(rung, level):
    # glsim and precheck check the tile against the rung's tt_pins, which no
    # session sees, so every level must state the same pin map in one line
    import yaml
    pins = yaml.safe_load((rung_dir(rung) / "spec.yaml").read_text())["tt_pins"]
    line = tile_statement(rung, level)
    assert line, f"{level}: no single line naming tt_um_{rung}"
    assert line == tile_statement(rung, "spec.md"), f"{level} words the pin map differently"
    assert f"`tt_um_{rung}` wraps `{rung}`" in line
    stated = dict(re.findall(r"`(\w+)` (?:<-|->) `([^`]+)`", line))
    assert stated == {p: str(v) for p, v in pins.items()}, (stated, pins)


def test_pin_map_parser_catches_a_wrong_pin():
    line = tile_statement("counter8", "spec.md")
    good = dict(re.findall(r"`(\w+)` (?:<-|->) `([^`]+)`", line))
    bad = dict(re.findall(r"`(\w+)` (?:<-|->) `([^`]+)`", line.replace("`~rst_n`", "`rst_n`")))
    assert good["rst"] == "~rst_n" and bad["rst"] == "rst_n" and good != bad
