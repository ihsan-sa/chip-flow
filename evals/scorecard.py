#!/usr/bin/env python
"""scorecard.py - score the runs the evals already hold into a per-design
scorecard and a suite score with confidence intervals, and cost the full
suite (docs/design-evals.md sections 7 and 8).

    scorecard.py [--record] [--doc docs/design-evals.md] [--seeds N]
                 [--results-dir DIR] [--out FILE]
    scorecard.py --round DIR [--results-dir DIR] [--out FILE] [--md FILE]

It runs no design and no gate. It reads what ladder.py and bench.py already
wrote under evals/results/: the newest ladder result per (skill, rung, kind)
and the newest bench result per fixture. Each ladder result becomes one
design card scored on four areas, each in [0, 1]:

  signoff         the gates the run owes that are green (attest.py's list,
                  as ladder.py recorded it)
  function        the corpus held-out tests, which the run never saw
  verification    mutation kill rate and line coverage
  implementation  worst slack at or above zero across the STA corners, and
                  area against the corpus reference's (1.0 at or below it)

An area the skill owes but the run never measured scores 0, because a gate
that did not run is a refusal, never a pass. vde owes all four; ade and msde
owe signoff and function only, since ladder.py records no analog margin or
msde measure yet, and their function area stays unmeasured (no held-out
gate) rather than scored. The composite is the mean of the owed areas that
have a score. Hand edits, owner rulings, fix attempts, cost and wall time
are process, reported beside the score and never in it.

Each card carries its findings in the shape a skill reads mid-design:
{gate, severity, what, fix, route_to}, where route_to is the agent role
fix_dispatch.py would send that gate's work to. The suite score per skill
is the mean composite over its skill runs (never reference runs) with a
seeded bootstrap 95% interval over designs, and the share of rungs that
count with a Wilson 95% interval. Bench results are listed as stage
scores beside the designs; they are frozen stages, not designs, so they do
not enter the suite score.

The cost estimate multiplies the recorded per-run cost by the full suite's
size: every rung of every skill x 3 brief detail levels x 2 arms (bare
Claude Code and the skill) x --seeds (default 3), and the same with one
rung a skill as the smaller first step. Only runs whose ladder
result carries cost_usd are used, and the estimate says how many those are.

`--round DIR` scores a bare-against-skill round instead
(docs/design-evals.md section 3): the results under DIR (DIR/ladder/*.json
and DIR/*.json) that carry an `arm` field, newest per (skill, rung, arm,
detail, repeat), each made a design card as above (area against the
--results-dir reference; a result with `hardened: false` scores 0 on
implementation). Skill and bare results are paired by (skill, rung, detail,
repeat), and it reports, per detail level and pooled, the mean difference
skill minus bare in the rate the rung counts, each area score and the
composite; the detail steps typical-terse and full-typical per arm, paired
by (rung, repeat); and cost_usd and wall_s per run and per arm (mean,
total). Every interval is a seeded cluster bootstrap: the cluster is the
pair (rung, detail, repeat), resampled in two stages (rungs, then that
rung's pairs), which with one rung is resampling the pairs. A result with
no partner is listed as unpaired and left out of the differences. It writes
the JSON to DIR/round-scorecard.json (or --out) and a short markdown summary
to DIR/round-scorecard.md (or --md). Seeded, so the same results give the
same report.

`--record` writes the scorecard as a dated JSON under
evals/results/scorecard/. `--doc` rewrites the text between the
scorecard:begin/end and cost:begin/end markers in that file from this run.
Exit 0 when it scored (the findings are part of the report, as ladder.py
--regen is), 2 error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import re
import statistics
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent
REPO = EVALS.parent
sys.path.insert(0, str(REPO / "engine" / "scripts"))
sys.path.insert(0, str(REPO / "engine" / "lib"))
import checklib  # noqa: E402
import fix_dispatch  # noqa: E402
import ladder  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "scorecard"
RESULTS = EVALS / "results"
DOC = REPO / "docs" / "design-evals.md"
AREAS = ("signoff", "function", "verification", "implementation")
OWED = {"vde": AREAS, "ade": ("signoff", "function"), "msde": ("signoff", "function")}
DETAIL_LEVELS = ("terse", "typical", "full")
ARMS = ("bare", "skill")
BOOT_N = 2000
BOOT_SEED = 1

# gate -> fix_dispatch domain; the role comes from fix_dispatch.ROLE_BY_DOMAIN
GATE_DOMAIN = {
    "mutate": "testbench", "cover": "testbench", "bench_strength": "testbench",
    "holdout": "rtl", "formal": "formal", "lint": "rtl", "sim": "rtl",
    "synth": "synth",
}
ADE_LAYOUT_GATES = {"drc", "lvs", "pex_sim", "layout"}


def route_to(skill: str, gate: str | None) -> str:
    if skill == "ade" and gate in ADE_LAYOUT_GATES:
        domain = "layout"
    else:
        domain = GATE_DOMAIN.get(gate or "", "harden")
    return fix_dispatch.ROLE_BY_DOMAIN.get(skill, {}).get(domain, "fixer")


def finding(skill: str, gate: str | None, what: str, fix: str,
            severity: str = "error") -> dict:
    return {"gate": gate, "severity": severity, "what": what, "fix": fix,
            "route_to": route_to(skill, gate)}


def gate_findings(skill: str, problems: list[str]) -> list[dict]:
    """ladder.py's gate_problems ("gate: why") as findings with a fix line."""
    out = []
    for p in problems:
        gate, _, why = p.partition(":")
        gate, why = (gate.strip(), why.strip()) if why else (None, p)
        cmd = f"/{skill} {gate}" if gate else f"/{skill} resume"
        if why == "not hardened":
            fix = (f"harden the block (/{skill} harden, LibreLane through eda) into "
                   "harden/runs/run/final/; a block with no GDS scores 0 on implementation")
        elif why == "no testbench":
            fix = ("write a cocotb testbench under tb/test_*.py; a run with no "
                   "testbench scores 0 on verification")
        elif "no recorded" in why and "pass" not in why:
            fix = f"run the {gate} gate ({cmd}); a gate that never ran is a refusal"
        elif "open issue" in why:
            fix = f"close the open issue: fix what it names and re-run its gate ({cmd})"
        else:
            fix = f"fix what {gate} reports, then re-run it ({cmd})"
        out.append(finding(skill, gate, p, fix))
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def design_card(r: dict, ref: dict | None) -> dict:
    skill, owed = r["skill"], OWED[r["skill"]]
    problems = r.get("gate_problems") or []
    bad = {p.partition(":")[0].strip() for p in problems if ":" in p}
    owes = set(r.get("gates_passed") or []) | bad
    areas: dict[str, float | None] = {}
    areas["signoff"] = (len(owes - bad) / len(owes)) if owes else 0.0
    if problems and not bad:  # an issue with no gate named still is not green
        areas["signoff"] = min(areas["signoff"], 0.0)
    held = r.get("held_out") or {}
    hs = held.get("status") if isinstance(held, dict) else held
    areas["function"] = {"pass": 1.0, "fail": 0.0}.get(hs)
    kr, line = r.get("kill_rate"), r.get("line_pct")
    areas["verification"] = _mean([kr if kr is not None else 0.0,
                                   line / 100 if line is not None else 0.0])
    slack, area = r.get("worst_slack_ns"), r.get("area")
    ref_area = (ref or {}).get("area")
    area_score = (min(1.0, ref_area / area) if area and ref_area
                  else (1.0 if area and r.get("kind") == "reference" else 0.0))
    areas["implementation"] = _mean([1.0 if slack is not None and slack >= 0 else 0.0,
                                     area_score])
    if r.get("hardened") is False:  # a --deliverables result with no GDS
        areas["implementation"] = 0.0
    for a in AREAS:
        if a not in owed:
            areas[a] = None
        elif areas[a] is None and not (a == "function" and hs == "n/a"):
            areas[a] = 0.0  # owed, never measured: a refusal
    scored = [areas[a] for a in owed if areas[a] is not None]
    composite = round(sum(scored) / len(scored), 4) if scored else 0.0

    found = gate_findings(skill, problems)
    if hs == "fail":
        found.append(finding(skill, "holdout", "the corpus held-out tests fail: "
                             + ", ".join(held.get("kinds") or []),
                             "fix the RTL against the requirement the failing held-out "
                             "test covers; the work order names it, never the test"))
    if skill == "vde" and kr is not None and kr < 1.0:
        found.append(finding(skill, "mutate", f"kill rate {kr:.2f}",
                             "add tests that kill the surviving mutant classes "
                             f"(/{skill} mutate lists them)", "warning"))
    if slack is not None and slack < 0:
        found.append(finding(skill, "timing", f"worst slack {slack} ns",
                             f"fix timing at the failing corner (/{skill} timing)"))
    for k, what in (("hand_edits", "hand edit(s)"),
                    ("rulings_undeclared", "undeclared mutant ruling(s)")):
        if r.get(k):
            found.append(finding(skill, None, f"{r[k]} {what}",
                                 "none for the agent: a hand edit stops the rung "
                                 "counting; record what the flow could not do",
                                 "info"))
    sev = {"error": 0, "warning": 1, "info": 2}
    found.sort(key=lambda f: sev[f["severity"]])
    return {
        "skill": skill, "rung": r["rung"], "kind": r.get("kind", "skill"),
        "scored_at": r.get("scored_at"), "phase": r.get("phase"),
        "counts": bool(r.get("counts")), "composite": composite,
        "areas": {a: (round(v, 4) if v is not None else None) for a, v in areas.items()},
        "process": {k: r.get(k) for k in ("hand_edits", "rulings", "fix_attempts",
                                          "cost_usd", "tokens", "wall_s")},
        "findings": found,
    }


def bootstrap_ci(xs: list[float], n: int = BOOT_N, seed: int = BOOT_SEED):
    if not xs:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(n))
    return [round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)]


def wilson(k: int, n: int, z: float = 1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def suite(cards: list[dict], rungs: dict) -> dict:
    out = {}
    for skill in OWED:
        mine = [c for c in cards if c["skill"] == skill and c["kind"] == "skill"]
        xs = [c["composite"] for c in mine]
        k = sum(c["counts"] for c in mine)
        out[skill] = {
            "rungs": len(rungs.get(skill) or []), "scored": len(mine),
            "composite": round(statistics.fmean(xs), 4) if xs else None,
            "composite_ci95": bootstrap_ci(xs),
            "counted": k, "counted_ci95": wilson(k, len(mine)),
        }
    allc = [c for c in cards if c["kind"] == "skill"]
    xs = [c["composite"] for c in allc]
    k = sum(c["counts"] for c in allc)
    out["all"] = {"scored": len(allc),
                  "composite": round(statistics.fmean(xs), 4) if xs else None,
                  "composite_ci95": bootstrap_ci(xs),
                  "counted": k, "counted_ci95": wilson(k, len(allc))}
    return out


def bench_rows(results: Path) -> list[dict]:
    newest = {}
    for p in sorted((results / "bench").glob("*.json")) if (results / "bench").is_dir() else []:
        b = json.loads(p.read_text(encoding="utf-8"))
        newest[(b.get("stage"), b.get("fixture"))] = (p.name, b)
    return [{"stage": s, "fixture": f, "file": name, "composite": b.get("composite"),
             "gates": {g: v.get("status") for g, v in (b.get("gates") or {}).items()}}
            for (s, f), (name, b) in sorted(newest.items())]


def cost_estimate(results: Path, rungs: dict, seeds: int) -> dict:
    d = results / "ladder"
    runs = [json.loads(p.read_text(encoding="utf-8"))
            for p in (sorted(d.glob("*.json")) if d.is_dir() else [])]
    runs = [r for r in runs if r.get("kind", "skill") == "skill"]
    costed = [r["cost_usd"] for r in runs if r.get("cost_usd")]
    walls = [r["wall_s"] for r in runs if r.get("wall_s")]
    n_rungs = sum(len(v) for v in rungs.values())
    n_runs = n_rungs * len(DETAIL_LEVELS) * len(ARMS) * seeds
    n_pilot = len(rungs) * len(DETAIL_LEVELS) * len(ARMS) * seeds  # one rung a skill
    per = {"min": min(costed), "median": statistics.median(costed),
           "max": max(costed)} if costed else None
    return {
        "rungs": n_rungs, "detail_levels": len(DETAIL_LEVELS), "arms": len(ARMS),
        "seeds": seeds, "runs": n_runs,
        "skill_runs_recorded": len(runs), "runs_with_cost": len(costed),
        "per_run_usd": per,
        "suite_usd": ({k: round(v * n_runs) for k, v in per.items()} if per else None),
        "one_rung_per_skill_runs": n_pilot,
        "one_rung_per_skill_usd": (round(per["median"] * n_pilot) if per else None),
        "median_wall_h": round(statistics.median(walls) / 3600, 1) if walls else None,
    }


# ---- --round: bare against skill, paired ----------------------------------

ROUND_METRICS = ("counts", "composite") + AREAS
DETAIL_STEPS = (("typical", "terse"), ("full", "typical"))


def load_round(d: Path) -> list[dict]:
    """The results under d (d/ladder/*.json and d/*.json) that carry an
    `arm`: the newest per (skill, rung, arm, detail, repeat)."""
    files = sorted(set((d / "ladder").glob("*.json")) | set(d.glob("*.json"))) \
        if d.is_dir() else []
    newest: dict[tuple, tuple[str, dict]] = {}
    for p in files:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(r, dict) or not r.get("arm") or not r.get("skill"):
            continue
        key = (r["skill"], r["rung"], r["arm"], r.get("detail"), r.get("repeat"))
        if key not in newest or p.name >= newest[key][0]:
            newest[key] = (p.name, r)
    return [r for _n, r in (newest[k] for k in sorted(newest, key=str))]


def metric_values(card: dict) -> dict:
    """counts as 0/1, the composite and each owed area score."""
    out = {"counts": 1.0 if card["counts"] else 0.0, "composite": card["composite"]}
    out.update({a: card["areas"][a] for a in AREAS})
    return out


def cluster_bootstrap(clusters: dict, n: int = BOOT_N, seed: int = BOOT_SEED):
    """95% interval of the mean of per-cluster values, resampled in two
    stages: rungs with replacement, then that rung's clusters (here, the
    pairs (rung, detail, repeat)) with replacement. With one rung that is
    resampling the pairs. `clusters` maps rung -> [value]."""
    groups = [v for _k, v in sorted(clusters.items()) if v]
    if not groups:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        xs = []
        for g in rng.choices(groups, k=len(groups)):
            xs.extend(rng.choices(g, k=len(g)))
        means.append(statistics.fmean(xs))
    means.sort()
    return [round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)]


def _diff_stats(diffs: list[tuple[str, dict]]) -> dict:
    """diffs: [(rung, {metric: a - b or None})] -> {metric: {mean, ci95, n}}."""
    out = {}
    for m in ROUND_METRICS:
        by_rung: dict[str, list[float]] = {}
        for rung, d in diffs:
            if d.get(m) is not None:
                by_rung.setdefault(rung, []).append(d[m])
        xs = [x for v in by_rung.values() for x in v]
        out[m] = {"mean": round(statistics.fmean(xs), 4) if xs else None,
                  "ci95": cluster_bootstrap(by_rung), "n": len(xs)}
    return out


def _sub(a: dict, b: dict) -> dict:
    return {m: (a[m] - b[m] if a.get(m) is not None and b.get(m) is not None else None)
            for m in ROUND_METRICS}


def _money(xs: list) -> dict:
    xs = [x for x in xs if isinstance(x, (int, float))]
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 2) if xs else None,
            "total": round(sum(xs), 2) if xs else None}


def round_report(results: list[dict], refs: dict) -> dict:
    """Paired differences skill minus bare per (skill, rung, detail, repeat),
    per detail level and pooled; detail-level steps per arm; cost and wall
    time per run and per arm."""
    cards = [(r, design_card(r, refs.get((r["skill"], r["rung"])))) for r in results]
    vals = {(r["skill"], r["rung"], r["arm"], r.get("detail"), r.get("repeat")):
            metric_values(c) for r, c in cards}
    skills = sorted({k[0] for k in vals})
    out: dict = {"skills": {}, "unpaired": []}
    for skill in skills:
        keys = {(k[1], k[3], k[4]) for k in vals if k[0] == skill}
        pairs = []
        for rung, detail, rep in sorted(keys, key=str):
            s_, b_ = (vals.get((skill, rung, a, detail, rep)) for a in ("skill", "bare"))
            if s_ is None or b_ is None:
                out["unpaired"].append({"skill": skill, "rung": rung, "detail": detail,
                                        "repeat": rep,
                                        "has": "skill" if s_ is not None else "bare"})
                continue
            pairs.append((rung, detail, _sub(s_, b_)))
        by_detail = {d: _diff_stats([(r, x) for r, dd, x in pairs if dd == d])
                     for d in DETAIL_LEVELS if any(dd == d for _r, dd, _x in pairs)}
        steps: dict = {}
        for arm in ARMS:
            for hi, lo in DETAIL_STEPS:
                diffs = []
                for rung, rep in sorted({(k[1], k[4]) for k in vals
                                         if k[0] == skill and k[2] == arm}, key=str):
                    a, b = (vals.get((skill, rung, arm, d, rep)) for d in (hi, lo))
                    if a is not None and b is not None:
                        diffs.append((rung, _sub(a, b)))
                if diffs:
                    steps.setdefault(arm, {})[f"{hi}-{lo}"] = _diff_stats(diffs)
        arms = {}
        for arm in ARMS:
            mine = [r for r, _c in cards if r["skill"] == skill and r["arm"] == arm]
            if mine:
                arms[arm] = {"runs": len(mine),
                             "counted": sum(1 for r in mine if r.get("counts")),
                             "cost_usd": _money([r.get("cost_usd") for r in mine]),
                             "wall_s": _money([r.get("wall_s") for r in mine])}
        out["skills"][skill] = {
            "pairs": len(pairs),
            "skill_minus_bare": {"by_detail": by_detail,
                                 "pooled": _diff_stats([(r, x) for r, _d, x in pairs])},
            "detail_steps": steps, "arms": arms}
    out["runs"] = [{**{k: r.get(k) for k in ("skill", "rung", "arm", "detail", "repeat",
                                              "model", "counts", "cost_usd", "wall_s")},
                    "composite": c["composite"], "areas": c["areas"],
                    "top_finding": c["findings"][0]["what"] if c["findings"] else None}
                   for r, c in cards]
    out["bootstrap"] = {"n": BOOT_N, "seed": BOOT_SEED,
                        "cluster": "the pair (rung, detail, repeat); resampled as "
                        "rungs, then each rung's pairs, with replacement"}
    return out


def _d(st: dict) -> str:
    if st["mean"] is None:
        return "-"
    return f"{st['mean']:+.2f} {_ci(st['ci95'])}"


def render_round(rep: dict) -> str:
    L = ["# Bare against skill: paired round summary", "",
         "Generated by `evals/scorecard.py --round`. Each cell is the mean "
         "difference with a cluster-bootstrap 95% interval "
         f"({rep['bootstrap']['n']} resamples, seed {rep['bootstrap']['seed']}; "
         f"cluster: {rep['bootstrap']['cluster']}). `counts` is the rate the rung "
         "counts; the rest are scorecard areas in [0, 1].", ""]
    head = "| | pairs | " + " | ".join(ROUND_METRICS) + " |"
    rule = "|---" * (len(ROUND_METRICS) + 2) + "|"
    for skill, v in rep["skills"].items():
        L += [f"## {skill}: skill minus bare", "", head, rule]
        rows = list(v["skill_minus_bare"]["by_detail"].items()) + [
            ("pooled", v["skill_minus_bare"]["pooled"])]
        for name, st in rows:
            n = max(x["n"] for x in st.values())
            L.append(f"| {name} | {n} | " + " | ".join(_d(st[m]) for m in ROUND_METRICS)
                     + " |")
        if v["detail_steps"]:
            L += ["", f"## {skill}: detail steps per arm", "",
                  "| arm | step | n | " + " | ".join(ROUND_METRICS) + " |",
                  "|---" * (len(ROUND_METRICS) + 3) + "|"]
            for arm, steps in v["detail_steps"].items():
                for step, st in steps.items():
                    n = max(x["n"] for x in st.values())
                    L.append(f"| {arm} | {step} | {n} | "
                             + " | ".join(_d(st[m]) for m in ROUND_METRICS) + " |")
        L += ["", f"## {skill}: cost and wall time per arm", "",
              "| arm | runs | counted | cost USD mean | cost USD total | wall s mean "
              "| wall s total |", "|---|---|---|---|---|---|---|"]
        for arm, a in v["arms"].items():
            L.append(f"| {arm} | {a['runs']} | {a['counted']} | {_f(a['cost_usd']['mean'])} "
                     f"| {_f(a['cost_usd']['total'])} | {_f(a['wall_s']['mean'], 0)} "
                     f"| {_f(a['wall_s']['total'], 0)} |")
    L += ["", "## Runs", "",
          "| rung | arm | detail | repeat | counts | composite | cost USD | wall s "
          "| top finding |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rep["runs"]:
        L.append(f"| {r['skill']}/{r['rung']} | {r['arm']} | {r['detail']} | "
                 f"{_f(r['repeat'])} | {'yes' if r['counts'] else 'no'} | "
                 f"{_f(r['composite'])} | {_f(r['cost_usd'])} | {_f(r['wall_s'], 0)} | "
                 f"{r['top_finding'] or '-'} |")
    if rep["unpaired"]:
        L += ["", "Unpaired (no partner arm, left out of the differences): "
              + ", ".join(f"{u['rung']} {u['detail']} r{u['repeat']} ({u['has']} only)"
                          for u in rep["unpaired"]) + "."]
    return "\n".join(L) + "\n"


# ---- the doc's generated blocks --------------------------------------------

def _f(v, nd=2):
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def _ci(c):
    return "-" if not c else f"[{c[0]:.2f}, {c[1]:.2f}]"


def render_scorecard(card: dict) -> str:
    L = [f"Generated by `evals/scorecard.py --doc` from the newest ladder and bench "
         f"results; do not edit by hand.", "",
         "| skill | designs scored | composite | 95% CI | rungs counted | 95% CI |",
         "|---|---|---|---|---|---|"]
    for s, v in card["suite"].items():
        L.append(f"| {s} | {v['scored']} | {_f(v['composite'])} | {_ci(v['composite_ci95'])} "
                 f"| {v['counted']}/{v['scored']} | {_ci(v['counted_ci95'])} |")
    L += ["", "| design | kind | phase | counts | composite | signoff | function "
          "| verification | implementation | top finding |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for c in card["designs"]:
        a = c["areas"]
        top = c["findings"][0]["what"] if c["findings"] else "-"
        L.append(f"| {c['skill']}/{c['rung']} | {c['kind']} | {c['phase']} "
                 f"| {'yes' if c['counts'] else 'no'} | {_f(c['composite'])} | "
                 + " | ".join(_f(a[x]) for x in AREAS) + f" | {top} |")
    if card["benches"]:
        L += ["", "| stage bench | composite | gates not passing |", "|---|---|---|"]
        for b in card["benches"]:
            bad = [g for g, s in b["gates"].items() if s != "pass"]
            L.append(f"| {b['stage']} {b['fixture']} | {_f(b['composite'])} "
                     f"| {', '.join(bad) or '-'} |")
    return "\n".join(L)


def render_cost(e: dict) -> str:
    per = e["per_run_usd"]
    L = [f"Generated by `evals/scorecard.py --doc`; do not edit by hand.", "",
         f"The full suite is {e['rungs']} rungs x {e['detail_levels']} detail levels "
         f"x {e['arms']} arms x {e['seeds']} seeds = **{e['runs']} design runs**.", ""]
    if per:
        L.append(f"Of {e['skill_runs_recorded']} skill runs on record, {e['runs_with_cost']} "
                 f"carry a cost: ${per['min']:.0f} to ${per['max']:.0f} a run, median "
                 f"${per['median']:.0f}. At those figures the suite costs "
                 f"${e['suite_usd']['min']:,} to ${e['suite_usd']['max']:,}, median "
                 f"${e['suite_usd']['median']:,}. One rung per skill instead of every "
                 f"rung is {e['one_rung_per_skill_runs']} runs, about "
                 f"${e['one_rung_per_skill_usd']:,} at the median.")
    else:
        L.append("No skill run on record carries a cost, so the suite cannot be costed.")
    if e["median_wall_h"] is not None:
        L.append(f"The median recorded wall time is {e['median_wall_h']} h a run, "
                 "which includes waits on owner rulings and blocked fixes.")
    return "\n".join(L)


def rewrite_doc(path: Path, card: dict) -> None:
    text = path.read_text(encoding="utf-8")
    for tag, body in (("scorecard", render_scorecard(card)),
                      ("cost", render_cost(card["cost_estimate"]))):
        pat = re.compile(rf"(<!-- {tag}:begin -->\n).*?(<!-- {tag}:end -->)", re.S)
        if not pat.search(text):
            raise CheckError(f"{path.name} has no <!-- {tag}:begin/end --> markers")
        text = pat.sub(lambda m: m.group(1) + body + "\n" + m.group(2), text)
    path.write_text(text, encoding="utf-8")


def build(results: Path, seeds: int) -> dict:
    rungs = ladder.load_ladder()
    refs = ladder.latest_ladder_results(results, "reference")
    cards = []
    for kind in ("skill", "reference"):
        for key, r in sorted(ladder.latest_ladder_results(results, kind).items()):
            cards.append(design_card(r, refs.get(key)))
    return {"script": SCRIPT, "status": "pass",
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "suite": suite(cards, rungs), "designs": cards,
            "benches": bench_rows(results),
            "cost_estimate": cost_estimate(results, rungs, seeds)}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-dir", default=str(RESULTS))
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--record", action="store_true",
                    help="write the scorecard under evals/results/scorecard/")
    ap.add_argument("--doc", help="rewrite the generated blocks in this file")
    ap.add_argument("--out", help="write the JSON here instead of stdout")
    ap.add_argument("--round", metavar="DIR",
                    help="pair the bare and skill results under DIR (those with an "
                    "arm field) instead; writes DIR/round-scorecard.json (or --out) "
                    "and DIR/round-scorecard.md (or --md)")
    ap.add_argument("--md", help="--round: write the markdown summary here")
    args = ap.parse_args(argv)
    if args.seeds < 1:
        raise CheckError("--seeds must be at least 1")
    results = Path(args.results_dir)
    if args.round:
        rdir = Path(args.round)
        found = load_round(rdir)
        if not found:
            raise CheckError(f"no result with an arm field under {rdir} or {rdir}/ladder")
        rep = {"script": SCRIPT, "status": "pass", "round": str(rdir),
               **round_report(found, ladder.latest_ladder_results(results, "reference"))}
        md = Path(args.md) if args.md else rdir / "round-scorecard.md"
        md.write_text(render_round(rep), encoding="utf-8")
        rep["md"] = str(md)
        return rep, args.out or str(rdir / "round-scorecard.json")
    card = build(results, args.seeds)
    if args.record:
        (results / "scorecard").mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
        p = results / "scorecard" / f"{stamp}_suite.json"
        p.write_text(json.dumps(card, indent=1) + "\n", encoding="utf-8")
        card["result_file"] = p.name
    if args.doc:
        rewrite_doc(Path(args.doc), card)
    return card, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
