"""Fault: one via removed from the GDS (gates.yaml's lvs row, docs/design.md
"### M4."). Hardens the untouched RTL first (`lvs` reads harden's own
output on disk, not anything faults.py records, so this fault runs
check_harden.run() itself before the target gate sees it).

check_lvs.py's own docstring explains why this corrupts the extracted
netlist TEXT rather than the GDS geometry directly: a from-scratch
`extract all` on a hand-edited GDS was tried first here and does not
reproduce LibreLane's own pin naming/property setup closely enough to
match even the clean design, so check_lvs.py compares against LibreLane's
OWN extraction (`final/spice/*.spice`) instead - meaning a fault has to
land there too, or it would never be seen by the comparison this gate
actually runs. Splitting one internal net name into two, at exactly one of
its occurrences, has the same electrical effect a missing via would: the
two device terminals that used to share a node no longer do."""
import re
from pathlib import Path

INSTANCE_RE = re.compile(r"^[Xx]\S+\s+(.*)\s+(\S+)\s*$")
GLOBAL_NETS = {"VPWR", "VGND", "VNW", "VPW", "VDD", "VSS"}


def plant(ws: Path) -> None:
    import check_harden
    payload, _out = check_harden.run(["--workspace", str(ws)])
    if payload.get("status") != "pass":
        raise RuntimeError(
            f"plant_lvs.py: harden did not pass on the untouched design "
            f"(status {payload.get('status')!r}) - the fault needs a "
            f"clean harden output to corrupt: {payload.get('violations')}")

    import ttlib
    top = ttlib.wrapper_name({"top": "counter8"})
    spice = ws / "harden" / "runs" / "run" / "final" / "spice" / f"{top}.spice"
    if not spice.is_file():
        raise RuntimeError(f"plant_lvs.py: no extracted spice at {spice}")

    lines = spice.read_text(encoding="utf-8").splitlines()
    counts: dict[str, int] = {}
    for line in lines:
        m = INSTANCE_RE.match(line)
        if not m:
            continue
        for net in m.group(1).split():
            if net.upper() in GLOBAL_NETS or net.startswith(("ui_in", "uo_out",
                                                              "uio_", "ena",
                                                              "clk", "rst_n")):
                continue
            counts[net] = counts.get(net, 0) + 1

    candidates = sorted((n for n, c in counts.items() if c >= 2),
                        key=lambda n: -counts[n])
    if not candidates:
        raise RuntimeError(
            f"plant_lvs.py: no internal net in {spice} appears on 2+ "
            "instance lines to split")
    victim = candidates[0]
    replacement = f"{victim}_via_removed"

    # split the LAST occurrence only, as a whole token (never a substring
    # of a longer net name) - the other occurrence(s) keep the real name,
    # so the two device terminals that used to share this node no longer
    # do, exactly what a missing via does electrically.
    token_re = re.compile(rf"(?<![\w]){re.escape(victim)}(?![\w])")
    last_idx = max(i for i, ln in enumerate(lines)
                   if INSTANCE_RE.match(ln) and token_re.search(ln))
    lines[last_idx] = token_re.sub(replacement, lines[last_idx], count=1)
    spice.write_text("\n".join(lines) + "\n", encoding="utf-8")
