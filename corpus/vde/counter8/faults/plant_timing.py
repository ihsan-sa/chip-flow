"""Fault: a chain that misses the spec's clock (gates.yaml's timing row,
docs/design.md "### M4."). counter8's own RTL is untouched - the fault is
spec.yaml's CLOCK_PERIOD shrunk to 2ns (500MHz), well under the design's
real critical path (the clean reference needs 20ns even with margin; the
original 10ns already left a -0.063ns setup violation, docs/design.md
corpus/vde/counter8/spec.yaml's own note) - so LibreLane's own harden flow
still completes cleanly (a period this design cannot meet is not a
placement/routing failure, just a bad number), and `timing`'s own
independent OpenSTA re-check is what actually catches it.

check_timing.py (this gate) reads harden's own output on disk, not
anything faults.py records - so this fault runs check_harden.run() itself,
in-process, to produce that output before the target gate (`timing`) is
asked to evaluate it."""
from pathlib import Path


def plant(ws: Path) -> None:
    spec_path = ws / "spec" / "spec.yaml"
    text = spec_path.read_text(encoding="utf-8")
    text = text.replace("period_ns: 20", "period_ns: 2")
    if "period_ns: 2\n" not in text:
        raise RuntimeError(
            f"{spec_path}: expected a 'period_ns: 20' line to shrink, "
            "found none - has counter8's spec.yaml changed?")
    spec_path.write_text(text, encoding="utf-8")

    import check_harden
    payload, _out = check_harden.run(["--workspace", str(ws)])
    if payload.get("status") != "pass":
        raise RuntimeError(
            f"plant_timing.py: harden itself did not pass on the shrunk-"
            f"period design (status {payload.get('status')!r}) - the "
            "fault needs a clean harden output to check timing against: "
            f"{payload.get('violations')}")
