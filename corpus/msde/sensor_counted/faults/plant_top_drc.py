"""Fault: a planted spacing violation (gates.yaml's msde top_drc row).
Runs top_harden on the untouched design first - top_drc reads its GDS on
disk - then drops two Metal1 (GDS 34/0) boxes 5 nm apart into the top
cell, well under gf180mcuD's minimum Metal1 spacing and connected to
nothing, the same plant counter8's drc fault uses on a digital tile."""
from pathlib import Path


def plant(ws: Path) -> None:
    import check_top_harden
    payload, _out = check_top_harden.run(["--workspace", str(ws)])
    if payload.get("status") != "pass":
        raise RuntimeError(f"plant_top_drc.py: top_harden did not pass on "
                           f"the untouched design: {payload.get('violations')}")
    gds_dir = ws / "top" / "harden" / "runs" / "run" / "final" / "gds"
    gds = next(gds_dir.glob("*.gds"))

    import klayout.db as db
    layout = db.Layout()
    layout.read(str(gds))
    shapes = layout.top_cell().shapes(layout.layer(34, 0))
    shapes.insert(db.Box(0, 0, 200, 200))
    shapes.insert(db.Box(205, 0, 405, 200))  # 5 nm gap (dbu 0.001 um)
    layout.write(str(gds))
