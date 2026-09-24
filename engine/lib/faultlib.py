"""faultlib.py - shared fault-planting helpers for corpus faults/plant_*.py
scripts (docs/design.md "### M2."; section 3 describes the corpus faults
mechanism, faults.py is this milestone's driver for it).

A planted spec_lint/lint/mutate fault looks the same regardless of which
design it lands in - a requirement missing its check kind, a bare
combinational latch, a testbench with every assertion neutered - so the
mechanics live here once. The sim and holdout faults are NOT here: they are
a real behavioral bug in one specific design, and each corpus rung's own
`faults/plant_sim.py` / `plant_holdout.py` writes its own buggy RTL.

Every plant_*.py exposes `plant(ws: Path) -> None`, called by faults.py
against a scratch copy of the corpus rung (never the corpus itself).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml


def append_latch_stub(rtl_path: Path) -> None:
    """Insert a combinational always block with no else - the exact shape
    verilator's LATCH warning always catches - just before the file's own
    `endmodule`. Fault gates.yaml names for `lint`: "an always @* missing an
    else (latch)"."""
    text = rtl_path.read_text(encoding="utf-8")
    idx = text.rfind("endmodule")
    if idx == -1:
        raise ValueError(f"{rtl_path}: no 'endmodule' to insert before")
    stub = (
        "\n  // FAULT: planted latch (missing else) for the lint gate\n"
        "  reg fault_latch_bit;\n"
        "  always @(*) begin\n"
        "    if (rst)\n"
        "      fault_latch_bit = 1'b1;\n"
        "  end\n"
    )
    rtl_path.write_text(text[:idx] + stub + text[idx:], encoding="utf-8")


def strip_requirement_check(spec_yaml_path: Path, req_id: str | None = None) -> None:
    """Remove the 'check:' key from one requirement (the one named req_id,
    or the first, when req_id is None). Fault gates.yaml names for
    `spec_lint`: "a requirement with no way to check it"."""
    data = yaml.safe_load(spec_yaml_path.read_text(encoding="utf-8"))
    target = None
    for r in data.get("requirements") or []:
        if req_id is None or r.get("id") == req_id:
            target = r
            break
    if target is None:
        raise ValueError(f"{spec_yaml_path}: no matching requirement to fault "
                         f"(req_id={req_id!r})")
    target.pop("check", None)
    spec_yaml_path.write_text(yaml.safe_dump(data, sort_keys=False),
                              encoding="utf-8")


def weaken_all_tests(tb_dir: Path) -> None:
    """Replace every top-level 'assert ...' statement under tb_dir's
    test_*.py files with a harmless 'assert True'. Fault gates.yaml names
    for `mutate`: "a testbench that asserts nothing".

    A backslash-continued assert's continuation line(s) are dropped along
    with it - replacing only the first line and leaving a continuation
    behind produces a dangling, more-indented expression statement with no
    block opener before it: a SyntaxError, not a weaker test. That failure
    mode is a trap in itself (every mutant's test module then fails to
    import and every mutant looks "killed" - a 100% kill rate from a broken
    harness, the exact opposite of the fault this function plants) rather
    than a loud one, so plant_mutate.py corpus tests are written without
    backslash continuations in their asserts precisely to avoid relying on
    this being airtight - but this handles it either way."""
    assert_re = re.compile(r"^(\s*)assert\b.*$")
    for py in sorted(tb_dir.glob("test_*.py")):
        lines = py.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        i = 0
        while i < len(lines):
            m = assert_re.match(lines[i])
            if m:
                out.append(f"{m.group(1)}assert True")
                while lines[i].rstrip().endswith("\\"):
                    i += 1
                i += 1
                continue
            out.append(lines[i])
            i += 1
        py.write_text("\n".join(out) + "\n", encoding="utf-8")
