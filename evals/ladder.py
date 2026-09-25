#!/usr/bin/env python
"""ladder.py - score one skill run against the corpus ladder, and regenerate
evals/ladder.md (docs/design.md section 3, "### M6.").

    ladder.py --skill vde --rung uart --run WS --hand-edits N
              [--session-tokens T] [--session-cost-usd C] [--wall-s S]
              [--note TEXT]
    ladder.py --regen

It scores a run a session already made; it never drives an agent. From the
run's workspace it records whether every gate went green (attest.py's own
check: every gate the skill owes has a fresh pass or a bound waiver, no
open issue, plus a fresh `release` pass), then runs the corpus's own
held-out tests, which the run never saw, on a copy of the workspace: the
strong form of docs/design.md section 2. Beside those it records the kill
rate, area and worst slack from the gates' recorded facts, the fix attempts
(gate attempts past the first), the tokens and cost from the spawn ledger
plus the orchestrating session's own, and the wall time.

Hand edits cannot be read off a workspace, so the caller declares them with
`--hand-edits`; it is required. A rung COUNTS when every gate is green, the
held-out tests pass, and there were no hand edits.

Every scored run is a dated JSON under evals/results/ladder/. ladder.md is
rebuilt from the newest result per rung, the bench baselines under
evals/fixtures/, and the newest CVDP result under evals/results/cvdp/, so the
ade and msde columns fill in as their results land. Exit 0 the rung counts,
1 it does not (the findings say why), 2 error; `--regen` exits 0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

EVALS = Path(__file__).resolve().parent
REPO = EVALS.parent
ENGINE = REPO / "engine"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))
import attest  # noqa: E402
import checklib  # noqa: E402
import gate as gate_mod  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "ladder"
CORPUS = REPO / "corpus"
RESULTS = EVALS / "results"
SKIP_COPY = {"runs", "state_snapshots", "log", "__pycache__"}


def load_ladder() -> dict:
    import yaml
    return yaml.safe_load((EVALS / "ladder.yaml").read_text(encoding="utf-8"))


def _ts(s: str | None):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def gates_green(ws: Path, data: dict) -> list[str]:
    """Why the run is not all-green; empty when it is."""
    _body, problems = attest.build(ws)
    fresh = statelib.freshness_report(data, ws).get("gates", {})
    rel = (data.get("gates") or {}).get("release") or {}
    if rel.get("status") != "pass":
        problems.append("release: no recorded pass")
    elif not (fresh.get("release") or {}).get("fresh"):
        problems.append("release: its pass is stale")
    return problems


def run_holdout(ws: Path, skill: str, rung_dir: Path) -> dict:
    """The corpus's held-out tests on a copy of the run's workspace."""
    rows = gate_mod.load_gates(gate_mod.DEFAULT_GATES).get(skill) or {}
    if "holdout" not in rows:
        return {"status": "n/a", "why": f"{skill} has no holdout gate"}
    src = rung_dir / "holdout"
    if not src.is_dir():
        raise CheckError(f"corpus rung {rung_dir.name} has no holdout/")
    with tempfile.TemporaryDirectory(prefix="chip-flow-ladder-") as tmp:
        copy = Path(tmp) / ws.name
        shutil.copytree(ws, copy, ignore=lambda d, names: [
            n for n in names if n in SKIP_COPY])
        if (copy / "holdout").exists():
            shutil.rmtree(copy / "holdout")
        shutil.copytree(src, copy / "holdout")
        try:
            report = gate_mod.run_report_for_gate(rows["holdout"], copy)
        except Exception as exc:  # noqa: BLE001  a gate that did not run
            raise CheckError(f"the held-out tests could not run: {exc}") from exc
        result = gate_mod.evaluate("holdout", rows["holdout"], report)
    return {"status": result["status"],
            "tests_passed": report.get("tests_passed"),
            "tests_run": len(report.get("tests_run") or []),
            "kinds": sorted({v.get("kind") for v in result.get("failing", [])
                             if v.get("kind")})}


def facts(data: dict, gate: str) -> dict:
    return ((data.get("gates") or {}).get(gate, {}).get("last") or {}).get("facts") or {}


def worst_slack(corners) -> float | None:
    vals = [c.get("setup_ws") for c in (corners or {}).values()
            if isinstance(c, dict) and isinstance(c.get("setup_ws"), (int, float))]
    return min(vals) if vals else None


def score(args) -> tuple[dict, list[dict]]:
    ws = Path(args.run).resolve()
    rung_dir = CORPUS / args.skill / args.rung
    if not rung_dir.is_dir():
        raise CheckError(f"no corpus rung at corpus/{args.skill}/{args.rung}")
    data = checklib.load_json(ws / "state.json", "run state.json")
    if data.get("skill") != args.skill:
        raise CheckError(f"the run is a {data.get('skill')!r} workspace, not {args.skill!r}")

    green_problems = gates_green(ws, data)
    held = run_holdout(ws, args.skill, rung_dir)
    gates = data.get("gates") or {}
    spawns = data.get("spawns") or []
    tokens = sum(int(s.get("tokens") or 0) for s in spawns) + (args.session_tokens or 0)
    cost = sum(float(s.get("cost_usd") or 0) for s in spawns) + (args.session_cost_usd or 0)
    hist = [_ts(h.get("ts")) for h in data.get("history") or []]
    hist = [h for h in hist if h]
    wall = args.wall_s if args.wall_s is not None else (
        (max(hist) - min(hist)).total_seconds() if len(hist) > 1 else None)

    counts = (not green_problems and held["status"] in ("pass", "n/a")
              and args.hand_edits == 0)
    result = {
        "skill": args.skill, "rung": args.rung, "block": data.get("block"),
        "run": ws.name, "phase": data.get("phase"),
        "scored_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
        "gates_green": not green_problems,
        "gate_problems": green_problems,
        "gates_passed": sorted(g for g, e in gates.items() if e.get("status") == "pass"),
        "hand_edits": args.hand_edits,
        "held_out": held,
        "kill_rate": facts(data, "mutate").get("kill_rate"),
        "line_pct": facts(data, "cover").get("line_pct"),
        "area": facts(data, "synth").get("area"),
        "worst_slack_ns": worst_slack(facts(data, "timing").get("corners")),
        "fix_attempts": sum(max(0, int(e.get("attempts") or 0) - 1)
                            for e in gates.values()),
        "tokens": tokens or None,
        "cost_usd": round(cost, 2) if cost else None,
        "wall_s": round(wall) if wall is not None else None,
        "note": args.note,
    }
    violations = [checklib.violation("ladder", "error", None, args.rung,
                                     "gate_not_green", [], p, "attest")
                  for p in green_problems]
    if held["status"] == "fail":
        violations.append(checklib.violation(
            "ladder", "error", "holdout", args.rung, "holdout_failed", [],
            f"the corpus held-out tests fail ({', '.join(held['kinds'])})", "holdout"))
    if args.hand_edits:
        violations.append(checklib.violation(
            "ladder", "error", None, args.rung, "hand_edits", [],
            f"{args.hand_edits} hand edit(s) declared", "caller"))
    return result, violations


# ---- ladder.md -------------------------------------------------------------

def latest(dirpath: Path, pattern: str) -> Path | None:
    files = sorted(dirpath.glob(pattern)) if dirpath.is_dir() else []
    return files[-1] if files else None


def latest_ladder_results(results: Path) -> dict:
    out = {}
    for p in sorted((results / "ladder").glob("*.json")) if (results / "ladder").is_dir() else []:
        r = json.loads(p.read_text(encoding="utf-8"))
        out[(r["skill"], r["rung"])] = r  # sorted by dated name: newest wins
    return out


def _cell(ladder_row: dict, skill: str, res: dict | None) -> str:
    title = ladder_row["title"]
    if not (CORPUS / skill / ladder_row["rung"]).is_dir():
        return f"{title}: not in corpus"
    if res is None:
        return f"{title}: not run"
    return f"{title}: {'counts' if res['counts'] else 'red'}"


def _fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _why_red(r: dict) -> str:
    if r["counts"]:
        return ""
    bits = []
    if not r["gates_green"]:
        bits.append(f"{len(r['gate_problems'])} gate(s) not green")
    if r["held_out"]["status"] == "fail":
        bits.append("held-out fails")
    if r["hand_edits"]:
        bits.append("hand edits")
    return "; ".join(bits)


def render(results: Path, fixtures: Path) -> str:
    ladder = load_ladder()
    res = latest_ladder_results(results)
    skills = ["vde", "ade", "msde"]
    lines = [
        "# The ladder",
        "",
        "Generated by `evals/ladder.py --regen` from `evals/results/`; do not edit by hand.",
        "A rung counts when every gate is green, the corpus held-out tests pass and nobody",
        "edited the run by hand (docs/design.md section 3). Harder rungs are higher up.",
        "",
        "| level | vde | ade | msde |",
        "|---|---|---|---|",
    ]
    depth = max(len(ladder.get(s) or []) for s in skills)
    for i in reversed(range(depth)):
        cells = []
        for s in skills:
            rows = ladder.get(s) or []
            cells.append(_cell(rows[i], s, res.get((s, rows[i]["rung"])))
                         if i < len(rows) else "")
        lines.append(f"| {i + 1} | " + " | ".join(cells) + " |")

    for s in skills:
        lines += ["", f"## /{s}", ""]
        scored = [r for r in (ladder.get(s) or []) if (s, r["rung"]) in res]
        if not scored:
            lines.append(f"No /{s} run scored yet. `ladder.py --skill {s} --rung <rung> "
                         "--run <ws>` fills this table.")
            continue
        lines += ["| rung | counts | why not | held-out | kill rate | area | worst slack ns "
                  "| fix attempts | tokens | cost USD | wall s | scored | note |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for row in ladder.get(s) or []:
            r = res.get((s, row["rung"]))
            if r is None:
                lines.append(f"| {row['rung']} | not run | | | | | | | | | | | |")
                continue
            h = r["held_out"]
            held = h["status"] if h.get("tests_run") is None else \
                f"{h['status']} ({h.get('tests_passed')}/{h.get('tests_run')})"
            lines.append("| " + " | ".join([
                row["rung"], "yes" if r["counts"] else "no", _why_red(r), held,
                _fmt(r.get("kill_rate")), _fmt(r.get("area"), 1),
                _fmt(r.get("worst_slack_ns")), _fmt(r.get("fix_attempts")),
                _fmt(r.get("tokens")), _fmt(r.get("cost_usd")), _fmt(r.get("wall_s")),
                r["scored_at"][:10], r.get("note") or ""]) + " |")

    lines += ["", "## Per-stage benches", "",
              "Frozen fixtures under `evals/fixtures/<stage>/<name>/`; `bench.py --compare` "
              "fails a change that lowers a composite.", ""]
    bases = sorted(fixtures.glob("*/*/baseline.json")) if fixtures.is_dir() else []
    if not bases:
        lines.append("No baselines yet.")
    else:
        lines += ["| stage | fixture | baseline composite | gates | written |",
                  "|---|---|---|---|---|"]
        for b in bases:
            d = json.loads(b.read_text(encoding="utf-8"))
            gs = ", ".join(f"{g} {v['status']}" for g, v in d["gates"].items())
            lines.append(f"| {b.parent.parent.name} | {b.parent.name} | "
                         f"{d['composite']} | {gs} | {d.get('written', '-')} |")

    lines += ["", "## CVDP", ""]
    cv = latest(results / "cvdp", "*.json")
    if cv is None:
        lines.append("No CVDP result yet (`evals/cvdp/run.py`).")
    else:
        c = json.loads(cv.read_text(encoding="utf-8"))
        overall = c.get("pass_rate") if not isinstance(c.get("pass_rate"), dict) \
            else c["pass_rate"].get("overall")
        lines += [f"Newest run: `{cv.name}`. Pass rate {_fmt(overall)} on "
                  f"{c.get('selected', c.get('subset_size', '-'))} problems "
                  f"(limit {c.get('limit') or 'none'}), mode `{c.get('mode', '-')}`.", ""]
        if c.get("caveat"):
            lines += [f"Caveat: {c['caveat']}", ""]
        cats = c.get("by_category") or {}
        if cats:
            lines += ["| category | passed | total | rate |", "|---|---|---|---|"]
            for cat, v in sorted(cats.items()):
                lines.append(f"| {cat} | {v.get('passed')} | {v.get('total')} | "
                             f"{_fmt(v.get('rate'))} |")
        lines += ["", "The leaderboard figures to read this beside are in "
                  "`evals/cvdp/README.md`."]
    return "\n".join(lines) + "\n"


def regen(results: Path = RESULTS, fixtures: Path = EVALS / "fixtures",
          out: Path = EVALS / "ladder.md") -> Path:
    out.write_text(render(results, fixtures), encoding="utf-8")
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--regen", action="store_true", help="only rebuild ladder.md")
    ap.add_argument("--skill", choices=("vde", "ade", "msde"))
    ap.add_argument("--rung")
    ap.add_argument("--run", help="the run's block workspace")
    ap.add_argument("--hand-edits", type=int,
                    help="hand edits made during the run (required to score)")
    ap.add_argument("--session-tokens", type=int)
    ap.add_argument("--session-cost-usd", type=float)
    ap.add_argument("--wall-s", type=float)
    ap.add_argument("--note", help="what this run was, shown on ladder.md "
                    "(e.g. that it is not a skill run)")
    ap.add_argument("--results-dir", default=str(RESULTS))
    ap.add_argument("--fixtures-dir", default=str(EVALS / "fixtures"))
    ap.add_argument("--ladder-md", default=str(EVALS / "ladder.md"))
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)
    results = Path(args.results_dir)

    if args.regen:
        md = regen(results, Path(args.fixtures_dir), Path(args.ladder_md))
        return {"script": SCRIPT, "status": "pass", "ladder_md": md.name}, args.out
    missing = [f for f in ("skill", "rung", "run", "hand_edits")
               if getattr(args, f) is None]
    if missing:
        raise CheckError("scoring a run needs " + ", ".join(
            "--" + m.replace("_", "-") for m in missing) + " (or --regen)")
    result, violations = score(args)
    (results / "ladder").mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    res_path = results / "ladder" / f"{stamp}_{args.skill}_{args.rung}.json"
    res_path.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    regen(results, Path(args.fixtures_dir), Path(args.ladder_md))
    payload = {"script": SCRIPT, "status": "violations" if violations else "pass",
               "counts": checklib.summarize(violations), "violations": violations,
               "result": result, "result_file": res_path.name}
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
