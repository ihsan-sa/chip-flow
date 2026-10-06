#!/usr/bin/env python
"""estimate_bench_strength.py - an ADVISORY estimate of the bench_strength
kill rate from a random sample of mutants. It is not the gate.

    estimate_bench_strength.py --workspace DIR --sample N [--seed S]
                               [--jobs auto|N] [--timeout S] [--out FILE]

It runs check_bench_strength's own baselines, refusals and per-mutant
scoring (prepare, score_all: cheapest bench first, early stop, the same
worker pool) on N mutants drawn with random.Random(seed) from the sorted
mutant ids, and reports the sample's kill rate with a 95% Wilson score
interval. The rate is the raw one: mutant rulings are not applied, so a
ruled survivor counts as a survivor. Use it to size a full run, never to
stand for one.

It can never record or stand for a gate pass. Its report names
`estimate_bench_strength` as its script, so gate.py --report refuses it
for the bench_strength gate; it carries `gate_ran: false`; and it always
holds one info finding, `advisory_not_a_gate`, so it exits 1, never 0. A
gate that did not run is a refusal, never a pass: only
check_bench_strength.py over every mutant is the gate. Its scratch decks
go to log/bench_strength_estimate/, never the gate's log/bench_strength/.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "lib"))
import check_bench_strength as bs  # noqa: E402
import checklib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "estimate_bench_strength"
OUT_SUBDIR = "log/bench_strength_estimate"
Z95 = 1.959964


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """The Wilson score interval for k successes in n trials."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = (z / (1 + z * z / n)) * math.sqrt(
        p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, centre - half), min(1.0, centre + half)


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--sample", type=int, required=True,
                    help="how many mutants to draw")
    ap.add_argument("--seed", type=int, default=1,
                    help="random.Random seed for the draw (default 1)")
    ap.add_argument("--timeout", type=float, default=bs.DEFAULT_TIMEOUT)
    ap.add_argument("--jobs", default=os.environ.get(bs.JOBS_ENV, "auto"),
                    help="as check_bench_strength.py --jobs")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)
    if args.sample < 1:
        raise CheckError("--sample must be at least 1")
    jobs = bs.parse_jobs(args.jobs)

    ws = Path(args.workspace)
    state = bs.prepare(ws, args.timeout, OUT_SUBDIR)
    ids = sorted(state["plans"])
    drawn = sorted(random.Random(args.seed).sample(
        ids, min(args.sample, len(ids))))
    scored = bs.score_all(state, drawn, jobs)
    killed = sum(1 for e in scored.values() if e["killed"])
    lo, hi = wilson(killed, len(drawn))
    note = (f"ADVISORY: {killed}/{len(drawn)} sampled mutants killed (seed "
            f"{args.seed}, of {len(ids)}); 95% interval on the kill rate "
            f"{lo:.3f}-{hi:.3f}. This is not the bench_strength gate and "
            "records no pass - run check_bench_strength.py for that")
    finding = checklib.violation(
        SCRIPT, "info", None, None, "advisory_not_a_gate", [], note,
        "estimate_bench_strength")
    payload = checklib.report(
        SCRIPT, ws / "netlist", [finding], gate_ran=False,
        seed=args.seed, sample_size=len(drawn), total_mutants=len(ids),
        killed=killed, survived=len(drawn) - killed,
        kill_rate=round(killed / len(drawn), 6),
        kill_rate_ci95=[round(lo, 6), round(hi, 6)],
        bench_order=state["order"],
        mutants={mid: ({"killed": True, "killed_by": e["killed_by"]}
                       if e["killed"] else {"killed": False})
                 for mid, e in sorted(scored.items())})
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
