"""Fault: a wrong top module name in info.yaml (gates.yaml's precheck row,
docs/design.md "### M4."). Hardens the untouched RTL first (`precheck`
reads harden's own output on disk, not anything faults.py records, so this
fault runs check_harden.run() itself before the target gate sees it), then
sets CHIP_FLOW_PRECHECK_TOP_OVERRIDE - check_precheck.py's own test/fault
hook (its stage_precheck_run docstring has the full mechanism) - so its
staged info.yaml declares a name the staged GDS's own copied bytes (still
really `tt_um_counter8` inside) does not match. The env var is read once,
at the top of check_precheck.py's run(), so setting it here (this process
also runs the "precheck" gate faults.py calls right after planting, in the
same interpreter) reaches it without a subprocess."""
import os
from pathlib import Path


def plant(ws: Path) -> None:
    import check_harden
    payload, _out = check_harden.run(["--workspace", str(ws)])
    if payload.get("status") != "pass":
        raise RuntimeError(
            f"plant_precheck.py: harden did not pass on the untouched "
            f"design (status {payload.get('status')!r}) - the fault needs "
            f"a clean harden output: {payload.get('violations')}")

    os.environ["CHIP_FLOW_PRECHECK_TOP_OVERRIDE"] = "tt_um_counter8_wrongname"
