#!/usr/bin/env python
"""layout_gen.py - run a block's layout generator (docs/design.md 1.3, 5,
"### M9."; docs/spikes/glayout.md).

    layout_gen.py --workspace DIR [--block NAME] [--out FILE]

Layout is code (docs/design.md 5): imports `layout/gen_<block>.py`'s own
`generate() -> gf.Component` (gLayout's adapter is not used - the spike
found it does not run on the image's gdsfactory 9.51; the fallback is
generator code written directly against gdsfactory with the GF180 PDK's
own primitive cells, engine/lib/layoutlib.py's `gf180_cells()`) and writes
`layout/<block>.gds` plus an abstract (`layout/<block>.abstract.json`: pin
labels and their position, for macro use downstream - docs/design.md 5).

`build()` is what check_analog_drc.py/check_analog_lvs.py/check_pex_sim.py
import directly (gate.py's own dispatch pattern: import the sibling module,
call its function, never re-shell out to a second interpreter) - each of
those three gates needs a FRESH GDS from the CURRENT generator source
every run, because the generator is the source of truth a layout-fixer
edits, never the GDS (docs/design.md 5). No grid-snap happens here beyond
what each generator's own `layoutlib.finalize()` call already did - adding
a second, blanket snap on top would silently fix a generator's own
off-grid bug, which is exactly the class of bug DRC exists to catch.

CLI/exit contract: checklib's own (a bare generation run has no
"violations" to report, so this script's `run()` follows the plainer
check_env.py shape - JSON to stdout/--out, exit 0 ok, 2 error).
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import layoutlib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "layout_gen"


def block_of(ws: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    state_path = ws / "state.json"
    if state_path.is_file():
        data = checklib.load_json(state_path, "state.json")
        block = data.get("block")
        if block:
            return block
    raise CheckError(
        "cannot tell which block this is: give --block, or run inside a "
        "workspace whose state.json carries 'block'")


def load_generator(ws: Path, block: str):
    gen_path = ws / "layout" / f"gen_{block}.py"
    if not gen_path.is_file():
        raise CheckError(
            f"no layout generator at {gen_path} - a missing generator is a "
            "refusal, never a pass")
    # engine/lib on sys.path BEFORE importing the generator module, so its
    # own `import layoutlib` resolves here regardless of where gen_<block>.py
    # physically sits (the real corpus tree, or a faults.py scratch copy
    # under a tempdir with no engine/ alongside it at all).
    if str(ENGINE / "lib") not in sys.path:
        sys.path.insert(0, str(ENGINE / "lib"))
    mod_name = f"gen_{block}_{abs(hash(str(gen_path)))}"
    spec = importlib.util.spec_from_file_location(mod_name, gen_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "generate"):
        raise CheckError(f"{gen_path} has no generate() function")
    return mod


def write_abstract(gds_path: Path, topcell: str, out_path: Path) -> dict:
    """Every text label on the WRITTEN GDS becomes one pin entry - the
    abstract a macro placement (M10's LibreLane macro, docs/design.md 5)
    or an LVS reference would read pin names/positions from without
    re-running the generator. Reads the GDS back with plain klayout.db
    (not gdsfactory's Component/kcl wrapper) - the same pattern
    layoutlib.parse_drc_rdb's callers use elsewhere in this module,
    because kcl's own higher-level layer registration does not agree with
    a layer only ever touched through add_text_label()'s raw kdb.Text
    insert."""
    import klayout.db as kdb

    layout = kdb.Layout()
    layout.read(str(gds_path))
    cell = layout.cell(topcell)
    if cell is None:
        raise CheckError(f"{gds_path} has no cell named {topcell!r}")
    pins = []
    for (layer, datatype), name in (
        (layoutlib.GF180_LAYER["metal1_label"], "metal1"),
        (layoutlib.GF180_LAYER["metal2_label"], "metal2"),
    ):
        li = layout.find_layer(layer, datatype)
        if li is None:
            continue
        # generate()'s own layoutlib.finalize() already flattens the whole
        # design, so the topcell holds every
        # shape directly - a plain top-level shape iterator is enough, no
        # instance-transform composition needed.
        it = cell.shapes(li).each()
        for s in it:
            if s.is_text():
                pins.append({"name": s.text_string, "layer": name,
                            "x": round(s.text_dtrans.disp.x, 4),
                            "y": round(s.text_dtrans.disp.y, 4)})
    bbox = cell.dbbox()
    abstract = {
        "cell": topcell,
        "bbox_um": [round(bbox.left, 4), round(bbox.bottom, 4),
                    round(bbox.right, 4), round(bbox.top, 4)],
        "pins": sorted(pins, key=lambda p: p["name"]),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(abstract, indent=1), encoding="utf-8")
    return abstract


def build(ws: Path, block: str | None = None):
    """Returns (gds_path, topcell_name, abstract_path, abstract_dict).
    Imported directly by check_analog_drc.py/check_analog_lvs.py/
    check_pex_sim.py - each calls this fresh rather than trusting a GDS
    already on disk."""
    block = block_of(ws, block)
    layout_dir = ws / "layout"
    layout_dir.mkdir(parents=True, exist_ok=True)
    gds_path = layout_dir / f"{block}.gds"
    # A generator (or gdsfactory/glayout under it) that print()s would put
    # text ahead of the caller's JSON on stdout; send it to stderr instead.
    with contextlib.redirect_stdout(sys.stderr):
        mod = load_generator(ws, block)
        comp = mod.generate()
        comp.write_gds(str(gds_path))
    abstract_path = layout_dir / f"{block}.abstract.json"
    abstract = write_abstract(gds_path, comp.name, abstract_path)
    return gds_path, comp.name, abstract_path, abstract


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--block", help="override state.json's 'block'")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    gds_path, topcell, abstract_path, abstract = build(
        ws, args.block)
    payload = {
        "script": SCRIPT, "status": "pass",
        "gds": str(gds_path.relative_to(ws)),
        "topcell": topcell,
        "abstract": str(abstract_path.relative_to(ws)),
        "pins": [p["name"] for p in abstract["pins"]],
    }
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
