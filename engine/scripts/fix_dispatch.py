"""fix_dispatch.py - turn a failed gate into fixer work orders.

Ported from /hwde's scripts/fix_dispatch.py (docs/design.md 1.3), domain
swapped: the uniform fix-loop protocol is unchanged (a gate fails -> cluster
the failing findings, cluster_violations.py -> ONE fixer agent per cluster,
where clusters don't share a file they may run in parallel -> fixers edit
via scripts only -> re-run the gate), but the fixer taxonomy is chip-flow's
own (rtl/testbench/formal/synth/harden/layout/sizing/review, docs/design.md
1.9's agent roles) instead of PCB's (router/placement/plane/silk/...), and
"region" (a board bbox) is gone - clusters key on (file, module, kind).

DOMAINS' `scripts` lists are placeholders at M1: the skill directories that
would hold rtl-writer/fixer/etc. agents and their real tool scripts do not
exist yet (docs/design.md, "### M1." - "No skill directories"). Each lists
the engine scripts a fixer in that domain can already reach; a skill
milestone (M2+) adds the real per-domain tool as it lands.

CLI: fix_dispatch.py --input gate_result.json --workspace WS
       [--out-dir DIR] [--state state.json] [--out summary.json]
Exit: 0 nothing to dispatch, 1 orders written (work to do), 2 error.

Work order shape (log/workorders/wo-<id>.json):
    {"id", "gate", "phase", "workspace", "fixer", "role_prompt",
     "allowed_scripts": [...], "guidance": [...], "remediations": [...],
     "cluster": {file, module, kinds, checks, severity, count, violations[]},
     "artifacts": {name: path...}, "scope":
     "fix ONLY these findings; do not touch unrelated files"}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(ENGINE / "lib"))
sys.path.insert(0, str(SCRIPTS))

import checklib  # noqa: E402
import cluster_violations  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "fix_dispatch"

# Per-domain script whitelists (the prompt lists the scripts the agent may
# use, in order of preference) and the load-bearing guidance lines.
DOMAINS: dict[str, dict] = {
    "rtl": {
        "scripts": ["engine/scripts/gate.py", "engine/scripts/state.py"],
        "guidance": [
            "Edit rtl/ only; re-run the failed gate (lint/sim/mutate/formal/"
            "cover/synth as applicable) when done.",
            "A finding from `mutate` means the TESTBENCH missed something, "
            "not the RTL - route mutate failures to the testbench domain "
            "instead of editing rtl/ to satisfy a weak test.",
        ],
    },
    "testbench": {
        "scripts": ["engine/scripts/gate.py", "engine/scripts/state.py"],
        "guidance": [
            "Edit tb/ (and the reference model it scores against) only; a "
            "mutate survivor names the mutant class the test must now kill.",
        ],
    },
    "formal": {
        "scripts": ["engine/scripts/gate.py", "engine/scripts/state.py"],
        "guidance": [
            "Edit formal/ only; a bounded-not-proven result names the depth "
            "reached - widen the induction depth or fix the property, "
            "never loosen it to make the gate pass.",
        ],
    },
    "synth": {
        "scripts": ["engine/scripts/gate.py", "engine/scripts/state.py"],
        "guidance": [
            "A synth finding (latch, unmapped cell, combinational loop) is "
            "almost always an RTL defect surfacing late - fix rtl/ and "
            "re-run synth, don't hand-edit the netlist.",
        ],
    },
    "harden": {
        "scripts": ["engine/scripts/jobs.py", "engine/scripts/state.py"],
        "guidance": [
            "harden/timing/drc/lvs/glsim/precheck findings on the hardened "
            "design: adjust harden/config.json or the RTL, then re-run the "
            "harden job (jobs.py start --gate harden) - it can take a "
            "while, so re-check with jobs.py status rather than blocking.",
        ],
    },
    "layout": {
        "scripts": ["engine/scripts/gate.py", "engine/scripts/state.py"],
        "guidance": [
            "Edit the layout GENERATOR code (layout/gen_<block>.py), never "
            "the GDS by hand - there is no analog autorouter here.",
        ],
    },
    "sizing": {
        "scripts": ["engine/scripts/gate.py", "engine/scripts/state.py"],
        "guidance": [
            "Edit sizing/sizing.yaml within its declared bounds; a "
            "bench_strength survivor means the BOUNDS are too wide, not "
            "the sizing - route it to the testbench domain instead.",
        ],
    },
    "review": {
        "scripts": ["engine/scripts/state.py"],
        "guidance": [
            "No script owns this finding kind - triage: either identify "
            "the right domain and say so, or escalate to a human with a "
            "one-paragraph explanation.",
        ],
    },
}

SIDECARS = ["spec/spec.yaml"]

# Trigger-indexed knowledge: skills/<skill>/reference/remediations/<kind>.md,
# keyed by the FINDING type, never by topic. Skill directories don't exist
# yet at M1 (docs/design.md, "### M1."); resolved per-run from the
# workspace's own skill so this needs no change once they land.
REMEDIATION_GUIDANCE = (
    "Read the remediation reference(s) listed in `remediations` FIRST: what "
    "the finding means, known false-positive classes, the cheapest-first "
    "fix ladder, and the traps this project already hit. They are keyed to "
    "your cluster's kinds."
)


def remediation_dir(repo_root: Path, skill: str | None) -> Path | None:
    if not skill:
        return None
    d = repo_root / "skills" / skill / "reference" / "remediations"
    return d if d.is_dir() else None


def remediation_paths(kinds, rem_dir: Path | None = None) -> list[str]:
    """The remediation refs that exist for a cluster's kinds (sorted,
    unique). rem_dir is None until skill directories exist (M1) - always []."""
    if rem_dir is None:
        return []
    out = []
    for kind in sorted({k for k in (kinds or []) if k}):
        ref = rem_dir / f"{kind}.md"
        if ref.is_file():
            out.append(str(ref).replace("\\", "/"))
    return out


def load_input(path: Path) -> tuple[list[dict], dict]:
    """Accept a gate.py result (failing[]), a check_<gate>.py report
    (violations[]), or a cluster_violations payload (clusters[] -
    reclustered from their violations)."""
    data = checklib.load_json(path, "input report")
    meta = {"gate": data.get("gate"), "phase": data.get("phase")}
    if "failing" in data:
        return data["failing"], meta
    if "violations" in data and isinstance(data["violations"], list):
        return data["violations"], meta
    if "clusters" in data:
        vs = [v for c in data["clusters"] for v in c.get("violations", [])]
        return vs, meta
    raise CheckError(f"{path}: no failing/violations/clusters list found")


def review_fallback_from_source(clusters: list[dict], source: str) -> None:
    """Findings whose fixer fell back to 'review' but all share one gate
    `source` (a reviewer transcribed no domain): leave them in review - a
    human triages, same as /hwde's ERC fallback did for schematic-only
    findings with no kind. Kept as a hook for a future per-source default."""
    return


MERGE_MAX_SRC = 2   # clusters at/below this size are merge candidates
MERGE_CAP = 8        # max violations in one merged order


def merge_small_clusters(clusters: list[dict]) -> list[dict]:
    """Batch same-fixer clusters of <= MERGE_MAX_SRC violations into one
    cluster (capped at MERGE_CAP violations); larger clusters and lone
    candidates pass through untouched."""
    small: dict[str, list[dict]] = {}
    out: list[dict] = []
    for c in clusters:
        if c["count"] <= MERGE_MAX_SRC:
            small.setdefault(c["fixer"], []).append(c)
        else:
            out.append(c)
    for fixer, cands in small.items():
        if len(cands) == 1:
            out.append(cands[0])
            continue
        batch: list[dict] = []
        for c in cands + [None]:                    # None flushes the tail
            if c is not None and (not batch or
                    sum(b["count"] for b in batch) + c["count"] <= MERGE_CAP):
                batch.append(c)
                continue
            if len(batch) == 1:
                out.append(batch[0])
            elif batch:
                vs = [v for b in batch for v in b["violations"]]
                files = {b.get("file") for b in batch}
                sev = max((b["severity"] for b in batch),
                          key=lambda s: cluster_violations.SEV_RANK.get(s, 0))
                out.append({
                    "file": files.pop() if len(files) == 1 else None,
                    "module": None,
                    "kinds": sorted({k for b in batch for k in b["kinds"]}),
                    "checks": sorted({k for b in batch for k in b["checks"]}),
                    "severity": sev, "count": len(vs),
                    "fixer": fixer, "violations": vs,
                    "merged_from": len(batch),
                })
            batch = [c] if c is not None else []
    out.sort(key=lambda c: (-cluster_violations.SEV_RANK.get(c["severity"], 0),
                            -c["count"]))
    return out


def parallel_groups(orders: list[dict]) -> list[list[int]]:
    """Group order ids whose files don't overlap: orders inside one group
    are safe to run in parallel (docs/design.md 1.3: the clustering key is
    file/module/kind, so "regions don't overlap" becomes "files don't
    overlap"); groups run in sequence. An order with no file (e.g. a spec-
    level finding) is serialized (own group each)."""
    def files_of(o):
        vs = o["cluster"]["violations"]
        names = {v.get("file") for v in vs if v.get("file")}
        return names or None

    groups: list[dict] = []  # {"ids": [...], "files": [set, ...]}
    for o in orders:
        fs = files_of(o)
        if fs is None:
            groups.append({"ids": [o["id"]], "files": [None]})
            continue
        placed = False
        for g in groups:
            if all(f is not None and f.isdisjoint(fs) for f in g["files"]):
                g["ids"].append(o["id"])
                g["files"].append(fs)
                placed = True
                break
        if not placed:
            groups.append({"ids": [o["id"]], "files": [fs]})
    return [g["ids"] for g in groups]


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", required=True,
                    help="gate result / check report / cluster payload JSON")
    ap.add_argument("--workspace", required=True,
                    help="the block workspace the fixers operate in")
    ap.add_argument("--out-dir",
                    help="work-order dir (default <workspace>/log/workorders)")
    ap.add_argument("--state", help="state.json - register orders as open "
                                    "issues (ids allocated there)")
    ap.add_argument("--out", help="write summary JSON here instead of stdout")
    args = ap.parse_args(argv)

    workspace = Path(args.workspace)
    if not workspace.is_dir():
        raise CheckError(f"workspace not found: {workspace}")

    violations, meta = load_input(Path(args.input))
    clusters = cluster_violations.cluster(violations)
    clusters = merge_small_clusters(clusters)

    st = None
    skill = None
    state_path = Path(args.state) if args.state else (
        workspace / "state.json" if (workspace / "state.json").is_file()
        else None)
    if state_path is not None:
        import state as state_mod
        st = state_mod.State.load(state_path)
        skill = st.data.get("skill")

    out_dir = Path(args.out_dir) if args.out_dir else workspace / "log" / "workorders"

    artifacts = {"workspace": str(workspace).replace("\\", "/")}
    for name in SIDECARS:
        f = workspace / name
        if f.exists():
            artifacts[Path(name).stem] = str(f).replace("\\", "/")

    rem_dir = None
    if state_path is not None:
        # repo root: two levels above engine/scripts (engine/ -> repo root)
        repo_root = ENGINE.parent
        rem_dir = remediation_dir(repo_root, skill)

    orders: list[dict] = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    for c in clusters:
        domain = DOMAINS.get(c["fixer"], DOMAINS["review"])
        if st is not None:
            rec = st.open_issue({
                "gate": meta.get("gate"), "phase": meta.get("phase"),
                "fixer": c["fixer"], "kinds": c.get("kinds"),
                "severity": c.get("severity"), "count": c.get("count"),
                "work_order": None,
            })
            oid = rec["id"]
        else:
            oid = len(orders) + 1
        remediations = remediation_paths(c.get("kinds"), rem_dir)
        guidance = list(domain["guidance"])
        if remediations:
            guidance.insert(0, REMEDIATION_GUIDANCE)
        order = {
            "id": oid, "created": ts,
            "gate": meta.get("gate"), "phase": meta.get("phase"),
            "workspace": artifacts["workspace"], "fixer": c["fixer"],
            "role_prompt": (f"skills/{skill}/agents/fixer.md" if skill
                            else None),
            "allowed_scripts": domain["scripts"],
            "guidance": guidance,
            "remediations": remediations,
            "cluster": {k: c[k] for k in ("file", "module", "kinds", "checks",
                                          "severity", "count", "violations")},
            "artifacts": artifacts,
            "scope": "fix ONLY these findings; do not touch unrelated "
                     "files; re-run the failed gate when done",
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        wo_path = out_dir / f"wo-{oid}.json"
        wo_path.write_text(json.dumps(order, indent=1), encoding="utf-8")
        if st is not None:
            for rec in st.data["open_issues"]:
                if rec["id"] == oid:
                    rec["work_order"] = str(wo_path).replace("\\", "/")
        orders.append(order)

    if st is not None:
        st.save()

    by_domain: dict[str, int] = {}
    for o in orders:
        by_domain[o["fixer"]] = by_domain.get(o["fixer"], 0) + 1
    payload = {
        "script": SCRIPT,
        "status": "violations" if orders else "pass",
        "workspace": artifacts["workspace"], "gate": meta.get("gate"),
        "counts": {"orders": len(orders), "violations": len(violations),
                   "by_domain": by_domain,
                   "with_remediation": sum(1 for o in orders
                                           if o["remediations"])},
        "orders": [{"id": o["id"], "fixer": o["fixer"],
                    "severity": o["cluster"]["severity"],
                    "file": o["cluster"]["file"], "kinds": o["cluster"]["kinds"],
                    "count": o["cluster"]["count"],
                    "remediations": o["remediations"],
                    "work_order": str(out_dir / f"wo-{o['id']}.json")
                    .replace("\\", "/")}
                   for o in orders],
        "parallel_groups": parallel_groups(orders),
        "out_dir": str(out_dir).replace("\\", "/"),
    }
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
