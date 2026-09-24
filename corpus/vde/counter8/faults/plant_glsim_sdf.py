"""Fault: a flop whose clock-to-Q delay only the SDF knows about (gates.yaml's
glsim row, docs/design.md "### M4."). Hardens the untouched RTL first
(`glsim` reads harden's own output on disk, not anything faults.py records),
then rewrites one dffq's CLK->Q IOPATH in the nominal-corner SDF glsim
annotates to 7ns. tb/ clocks at 10ns and samples on the falling edge, 5ns
after the rising one, so with the SDF applied that bit reads a clock late,
while the functional (zero-delay) pass stays green. A gate that runs its
"sdf" pass without really annotating (no -gspecify, or an SDF it skipped)
passes this fault; that is the bug it guards."""
import re
from pathlib import Path

DELAY_NS = 7.0
IOPATH_RE = re.compile(
    r'(\(CELLTYPE\s+"[^"]*__dffq_\d+"\)\s*\(INSTANCE\s+[^)]+\)\s*\(DELAY\s*'
    r'\(ABSOLUTE\s*\(IOPATH\s+CLK\s+Q\s+)\([^)]*\)\s*\([^)]*\)')


def plant(ws: Path) -> None:
    import check_glsim
    import check_harden
    import ttlib
    payload, _out = check_harden.run(["--workspace", str(ws)])
    if payload.get("status") != "pass":
        raise RuntimeError(
            f"plant_glsim_sdf.py: harden did not pass on the untouched "
            f"design (status {payload.get('status')!r}) - the fault needs a "
            f"clean harden output: {payload.get('violations')}")

    top = ttlib.wrapper_name({"top": "counter8"})
    corner = check_glsim.SDF_CORNER
    sdf = (ws / "harden" / "runs" / "run" / "final" / "sdf" / corner /
           f"{top}__{corner}.sdf")
    text = sdf.read_text(encoding="utf-8")
    d = f"({DELAY_NS:.3f}:{DELAY_NS:.3f}:{DELAY_NS:.3f})"
    new, n = IOPATH_RE.subn(lambda m: f"{m.group(1)}{d} {d}", text, count=1)
    if n != 1:
        raise RuntimeError(f"plant_glsim_sdf.py: no dffq CLK->Q IOPATH in {sdf}")
    sdf.write_text(new, encoding="utf-8")
