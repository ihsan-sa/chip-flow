"""Fault: a metal spacing violation planted in the GDS (gates.yaml's drc
row, docs/design.md "### M4."). Hardens the untouched RTL first (`drc`
reads harden's own output on disk, not anything faults.py records, so this
fault runs check_harden.run() itself before the target gate sees it), then
opens the resulting GDS with klayout's python module and drops two
Metal1 (GDS layer 34/0 - engine/reference/tt's own gf180mcu.lyp) boxes 5nm
apart directly into the top cell: comfortably under gf180mcuD's real
minimum spacing on that layer, and unconnected to anything the design
actually routes, so it changes nothing about the design's function, only
its DRC cleanliness."""
from pathlib import Path

METAL1_LAYER, METAL1_DATATYPE = 34, 0


def plant(ws: Path) -> None:
    import check_harden
    payload, _out = check_harden.run(["--workspace", str(ws)])
    if payload.get("status") != "pass":
        raise RuntimeError(
            f"plant_drc.py: harden did not pass on the untouched design "
            f"(status {payload.get('status')!r}) - the fault needs a "
            f"clean harden output to corrupt: {payload.get('violations')}")

    import ttlib
    top = ttlib.wrapper_name({"top": "counter8"})
    gds = ws / "harden" / "runs" / "run" / "final" / "gds" / f"{top}.gds"
    if not gds.is_file():
        raise RuntimeError(f"plant_drc.py: no hardened GDS at {gds}")

    import klayout.db as db
    layout = db.Layout()
    layout.read(str(gds))
    top_cell = layout.top_cell()
    li = layout.layer(METAL1_LAYER, METAL1_DATATYPE)
    shapes = top_cell.shapes(li)
    shapes.insert(db.Box(0, 0, 200, 200))
    shapes.insert(db.Box(205, 0, 405, 200))  # 5nm gap (dbu=0.001um)
    layout.write(str(gds))
