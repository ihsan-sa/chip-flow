"""task_router.py - match a task to a verb and plan its recipe.

Ported from /hwde's scripts/task_router.py (docs/design.md 1.8), with the
one structural change the design calls for: "One task_router.py, called
with --skill. It loads the engine's shared verbs, then the skill's
tasks.yaml, where the skill's entry wins." /hwde had one board, one
tasks.yaml; chip-flow has three skills sharing an engine, so every lookup
here takes --skill and merges engine/reference/tasks.yaml (the shared verbs:
full-run, review, fix-finding, resume, release, learn) with
skills/<skill>/reference/tasks.yaml when that file exists (M1: it never
does yet - "No skill directories", docs/design.md "### M1." - so every
skill currently sees the shared table only).

Refdes/net/LCSC extraction (PCB-only) is gone; arg kinds are `path`, `int`,
`text`. Workspace slots come straight from invalidation.yaml's (flat)
artifact_kinds instead of KiCad's {board}-templated paths.

  task_router.py --skill vde --task "..." --workspace blocks/counter8
  task_router.py --skill vde --verb resume --workspace blocks/counter8
  task_router.py --skill vde --list            # the verb table
  task_router.py --skill vde --validate        # registry self-check

exit 0 planned / 1 needs a decision (ambiguous, unknown, missing args,
blocked precondition) / 2 error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT = "task_router.py"
SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import yaml  # noqa: E402

from checklib import CheckError, utf8_stdout  # noqa: E402
import statelib  # noqa: E402

TASKS = ENGINE / "reference" / "tasks.yaml"
GATES = ENGINE / "reference" / "gates.yaml"

STEP_KINDS = ("do", "gate", "agent", "human", "recipe", "note")
PRECONDITIONS = ("workspace", "no_open_issues")  # plus "gates_fresh:<gate>"

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def _skill_tasks_path(skill: str) -> Path:
    return REPO / "skills" / skill / "reference" / "tasks.yaml"


def load_tasks(skill: str, path: Path | str | None = None) -> dict:
    """Engine shared verbs merged with the skill's own (the skill's entry
    wins on a name collision). `path` overrides the ENGINE table only
    (tests)."""
    p = Path(path) if path else TASKS
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("verbs"), dict):
        raise CheckError(f"{p}: no verbs table")
    verbs = dict(data["verbs"])
    skill_path = _skill_tasks_path(skill)
    if skill_path.is_file():
        skill_data = yaml.safe_load(skill_path.read_text(encoding="utf-8"))
        verbs.update((skill_data or {}).get("verbs") or {})
    return {"version": data.get("version", 1), "verbs": verbs}


def load_gate_order(skill: str, path: Path | str | None = None) -> list[str]:
    """Gate names in PIPELINE order for this skill (by phase, then
    gates.yaml declaration order)."""
    p = Path(path) if path else GATES
    gates = yaml.safe_load(p.read_text(encoding="utf-8"))["gates"]
    rows = gates.get(skill) or {}
    order = list(rows)
    return sorted(order, key=lambda g: (str(rows[g].get("phase", "P9")),
                                        order.index(g)))


def validate_registry(skill: str, tasks: dict | None = None,
                      imap: dict | None = None) -> list[str]:
    """Structural problems in the merged tasks table, as human-readable
    strings. Empty = internally consistent and every artifact it names
    exists."""
    tasks = tasks or load_tasks(skill)
    imap = imap or statelib.load_map()
    edit_classes = imap["edit_classes"].get(skill) or {}
    gates = load_gate_order(skill)
    problems: list[str] = []

    for verb, spec in tasks["verbs"].items():
        where = f"verbs.{verb}"
        for field in ("summary", "doc", "workspace", "match"):
            if field not in spec:
                problems.append(f"{where}: missing {field!r}")
        if spec.get("workspace") not in ("required", "optional", "created", None):
            problems.append(f"{where}: bad workspace {spec.get('workspace')!r}")
        doc = spec.get("doc")          # repo-relative, or null at M1
        if doc and not (REPO / doc).is_file():
            problems.append(f"{where}: doc {doc} not found")

        cls = spec.get("edit_class")
        if cls is not None:
            if cls not in edit_classes:
                problems.append(f"{where}: unknown edit_class {cls!r} for "
                                f"skill {skill!r}")
            if "gates" in spec:
                problems.append(
                    f"{where}: has edit_class AND gates - gates come from "
                    "invalidation.yaml (restating them is how they drift)")
            if "human_hold" in spec:
                problems.append(
                    f"{where}: has edit_class AND human_hold - the weight "
                    "comes from invalidation.yaml")
        else:
            for g in spec.get("gates") or []:
                if g not in gates:
                    problems.append(f"{where}: unknown gate {g!r}")

        for name, arg in (spec.get("args") or {}).items():
            if not isinstance(arg, dict) or "kind" not in arg:
                problems.append(f"{where}.args.{name}: needs a kind")
            elif arg["kind"] not in ("path", "int", "text"):
                problems.append(f"{where}.args.{name}: bad kind {arg['kind']!r}")
            if arg.get("required") and not arg.get("question"):
                problems.append(f"{where}.args.{name}: required args need a question")
            if arg.get("extract", "only") not in ("only", "first", "none"):
                problems.append(f"{where}.args.{name}: bad extract policy "
                                f"{arg['extract']!r}")

        for pc in spec.get("preconditions") or []:
            base = pc.split(":", 1)[0]
            if base == "gates_fresh":
                if pc.split(":", 1)[1] not in gates:
                    problems.append(f"{where}: gates_fresh names unknown gate {pc!r}")
            elif pc not in PRECONDITIONS:
                problems.append(f"{where}: unknown precondition {pc!r}")

        for pat in (spec["match"].get("any", []) + spec["match"].get("not", [])
                    + spec["match"].get("all", [])):
            try:
                re.compile(pat)
            except re.error as exc:
                problems.append(f"{where}: bad regex {pat!r} ({exc})")
        if not spec["match"].get("any"):
            problems.append(f"{where}: match.any is empty - unreachable verb")

        bodies = [("steps", spec.get("steps"))]
        for vname, var in (spec.get("variants") or {}).items():
            if "when" not in var:
                problems.append(f"{where}.variants.{vname}: missing when")
            elif not _valid_condition(var["when"]):
                problems.append(
                    f"{where}.variants.{vname}: bad when {var['when']!r}")
            if var.get("edit_class") and var["edit_class"] not in edit_classes:
                problems.append(f"{where}.variants.{vname}: unknown edit_class")
            if var.get("edit_class") and ("gates" in var or "human_hold" in var):
                problems.append(f"{where}.variants.{vname}: edit_class AND "
                                "gates/human_hold - the map owns both")
            for g in var.get("gates") or []:
                if g not in gates:
                    problems.append(f"{where}.variants.{vname}: unknown gate {g!r}")
            bodies.append((f"variants.{vname}.steps", var.get("steps")))
        if not any(b for _, b in bodies):
            problems.append(f"{where}: no steps and no variants")

        for label, steps in bodies:
            if steps is None:
                continue
            if not isinstance(steps, list) or not steps:
                problems.append(f"{where}.{label}: empty")
                continue
            for i, step in enumerate(steps):
                problems += _validate_step(f"{where}.{label}[{i}]", step,
                                           tasks, gates)
    return problems


def _valid_condition(cond: str) -> bool:
    if cond in ("always", "has_workspace"):
        return True
    head, _, rest = cond.partition(":")
    if head == "has_arg" and rest:
        return True
    if head == "matches" and rest:
        try:
            re.compile(rest)
        except re.error:
            return False
        return True
    return False


_SCRIPT_INFO: dict[str, tuple[set[str], set[str]] | None] = {}


def _script_info(name: str):
    """(declared flags, subcommand names) for engine/scripts/<name>.py, or
    None. Declared = it appears in an `add_argument("--flag")` call."""
    if name in _SCRIPT_INFO:
        return _SCRIPT_INFO[name]
    script = SCRIPTS / f"{name}.py"
    info = None
    if script.is_file():
        src = script.read_text(encoding="utf-8")
        flags = set(re.findall(
            r"add_argument\(\s*[\"'](--[a-z][a-z0-9-]*)[\"']", src))
        subs = set(re.findall(r"add_parser\(\s*[\"']([a-z][a-z0-9_-]*)[\"']", src))
        for dyn in re.finditer(r"add_parser\(\s*[a-z_]+\s*\)", src):
            head = "\n".join(src[:dyn.start()].split("\n")[-3:])
            subs |= set(re.findall(r"[\"']([a-z][a-z0-9_-]*)[\"']", head))
        info = (flags, subs)
    _SCRIPT_INFO[name] = info
    return info


def _check_command_text(where: str, text: str) -> list[str]:
    """Validate flags inside PROSE, but only where the prose quotes a
    command: after a <script>.py, keep consuming subcommands, paths and
    placeholders, and stop at the first ordinary word."""
    problems: list[str] = []
    tokens = text.split()
    for i, tok in enumerate(tokens):
        raw = tok.strip("`\"'(),.;:")
        if not raw.endswith(".py"):
            continue
        info = _script_info(raw.split("/")[-1][:-3])
        if info is None:
            continue
        flags, subs = info
        for nxt in tokens[i + 1:]:
            t = nxt.strip("`\"'(),.;:")
            if t.startswith("--"):
                if t not in flags:
                    problems.append(f"{where}: {raw} declares no {t}")
                continue
            if t and (t in subs or t[0] in "{<-" or "/" in t):
                continue    # subcommand, placeholder, path or short flag
            break        # an ordinary word: the quoted command ended
    return problems


def _validate_step(where: str, step: dict, tasks: dict,
                   gates: list[str]) -> list[str]:
    problems: list[str] = []
    if not isinstance(step, dict):
        return [f"{where}: not a mapping"]
    kinds = [k for k in STEP_KINDS if k in step]
    if len(kinds) != 1:
        return [f"{where}: needs exactly one of {STEP_KINDS}, has {kinds}"]
    kind = kinds[0]
    if kind == "do":
        cmd = step["do"]
        m = re.match(r"scripts/([a-z_0-9]+\.py)\b", cmd)
        if not m:
            problems.append(f"{where}: `do` must start with scripts/<name>.py")
        else:
            info = _script_info(m.group(1)[:-3])
            if info is None:
                problems.append(f"{where}: no such script {m.group(1)}")
            else:
                flags, subs = info
                for flag in re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", cmd):
                    if flag not in flags:
                        problems.append(f"{where}: {m.group(1)} declares no {flag}")
                tokens = cmd.split()[1:]
                if subs and tokens and not tokens[0].startswith("-"):
                    if tokens[0] not in subs:
                        problems.append(f"{where}: {m.group(1)} has no "
                                        f"subcommand {tokens[0]!r}")
    elif kind == "gate":
        g = step["gate"]
        if "{" not in g and g not in gates:
            problems.append(f"{where}: unknown gate {g!r}")
    elif kind == "agent":
        if not step.get("tier"):
            problems.append(f"{where}: agent step needs a tier")
        # agent prompt files live under skills/<skill>/agents/, which does
        # not exist until M5 - existence is not checked here at M1.
    elif kind == "human":
        if not re.fullmatch(r"H[1-9][0-9]?", str(step["human"])):
            problems.append(f"{where}: human hold must be H<n>")
    elif kind == "recipe":
        if step["recipe"] not in tasks["verbs"]:
            problems.append(f"{where}: unknown recipe {step['recipe']!r}")
    for field in ("note", "why", "when"):
        if isinstance(step.get(field), str):
            problems += _check_command_text(f"{where}.{field}", step[field])
    return problems


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------
def match_verbs(text: str, tasks: dict) -> list[dict]:
    """Every verb the task text plausibly names, best first."""
    out = []
    for verb, spec in tasks["verbs"].items():
        m = spec["match"]
        if any(re.search(p, text, re.I) for p in m.get("not", [])):
            continue
        if not all(re.search(p, text, re.I) for p in m.get("all", [])):
            continue
        hits = [p for p in m.get("any", []) if re.search(p, text, re.I)]
        if not hits:
            continue
        out.append({"verb": verb, "score": len(hits) + int(m.get("weight", 0)),
                    "matched": hits, "summary": spec["summary"]})
    return sorted(out, key=lambda c: (-c["score"], c["verb"]))


# ---------------------------------------------------------------------------
# argument extraction (PCB refdes/net/LCSC extraction does not port - a
# chip-flow task text names paths, gate names and free text, nothing
# regex-recoverable the way a refdes is)
# ---------------------------------------------------------------------------
QUOTED_RE = re.compile(r"[\"']([^\"']+)[\"']")


def extract_args(text: str, spec: dict, cwd: Path) -> dict:
    got: dict[str, str] = {}
    for name, arg in (spec.get("args") or {}).items():
        kind = arg["kind"]
        policy = arg.get("extract", "only")
        val = None
        if policy == "none":
            continue
        if kind == "int":
            hits = {m for m in re.findall(r"\b(\d{1,5})\b", text)}
            if len(hits) == 1:
                val = hits.pop()
        elif kind == "path":
            cands = [t.strip(",;()") for t in QUOTED_RE.findall(text)]
            cands += [t.strip(",;()") for t in text.split()
                      if ("/" in t or t.endswith((".json", ".yaml", ".md")))]
            existing = [c for c in cands if (cwd / c).exists()]
            pick = existing or cands
            if len(set(pick)) == 1:
                val = pick[0]
        if val is not None:
            got[name] = val
    return got


# ---------------------------------------------------------------------------
# workspace context
# ---------------------------------------------------------------------------
DIR_SLOTS = {"reports": "reports", "log": "log"}


def workspace_context(ws: Path | None, imap: dict, skill: str | None = None) -> dict:
    """Slot bindings + live state for a workspace (which may not exist yet)."""
    ctx: dict = {"workspace": None, "block": None, "state": None,
                 "exists": False, "slots": {}}
    if ws is None:
        ctx["slots"] = {"skill": skill} if skill else {}
        return ctx
    ctx["workspace"] = str(ws).replace("\\", "/")
    state_path = ws / "state.json"
    registry = None
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckError(f"unreadable state.json: {exc}") from exc
        ctx["exists"] = True
        ctx["block"] = data.get("block")
        registry = data.get("artifacts") or {}
        ctx["state"] = data
    if not ctx["block"]:
        ctx["block"] = ws.name
    slots = {"ws": ctx["workspace"], "block": ctx["block"], "skill": skill,
             "state": f"{ctx['workspace']}/state.json"}
    for kind in imap["artifact_kinds"]:
        rel = statelib.kind_path(kind, imap, registry)
        slots[kind] = f"{ctx['workspace']}/{rel}"
    for slot, sub in DIR_SLOTS.items():
        slots[slot] = f"{ctx['workspace']}/{sub}"
    ctx["slots"] = slots
    return ctx


def infer_failing_gate(ctx: dict) -> tuple[str | None, str | None]:
    """(gate, report path) when the workspace has EXACTLY ONE failing gate
    and its report is on disk."""
    data = ctx.get("state")
    if not data:
        return None, None
    failing = [g for g, e in (data.get("gates") or {}).items()
               if e.get("status") == "fail"]
    if len(failing) != 1:
        return None, None
    gate = failing[0]
    rep = Path(ctx["slots"]["reports"]) / f"gate-{gate}.json"
    return gate, (str(rep).replace("\\", "/") if rep.is_file() else None)


def resume_view(ctx: dict) -> dict | None:
    """The freshness half of state.py resume, read-only and import-free."""
    data = ctx.get("state")
    if not data:
        return None
    ws = Path(ctx["workspace"])
    fresh = statelib.freshness_report(data, ws, statelib.load_map())
    gates = data.get("gates", {})
    return {
        "phase": data.get("phase"),
        "gates_passed": sorted(g for g, e in gates.items()
                               if e.get("status") == "pass"),
        "gates_stale": fresh["summary"]["stale"],
        "gates_freshness_unknown": fresh["summary"]["unknown"],
        "human_hold_pending": fresh["summary"]["human_hold_pending"],
        "open_issues": [i["id"] for i in data.get("open_issues", [])
                        if i.get("status") in ("open", "fixing")],
    }


def check_preconditions(spec: dict, ctx: dict, view: dict | None) -> list[dict]:
    out = []
    for pc in spec.get("preconditions") or []:
        base, _, arg = pc.partition(":")
        ok, detail = True, "ok"
        if not ctx["exists"]:
            ok, detail = False, "no workspace (state.json not found)"
        elif base == "workspace":
            pass
        elif base == "gates_fresh":
            view = view or {}
            if arg in (view.get("gates_stale") or []):
                ok, detail = False, f"gate {arg} is marked stale - re-run it"
            elif arg in (view.get("gates_freshness_unknown") or []):
                ok, detail = False, (f"gate {arg} freshness is unknown - re-run "
                                     "it to establish input hashes")
            elif arg not in (view.get("gates_passed") or []):
                ok, detail = False, f"gate {arg} has not passed"
            else:
                detail = f"gate {arg} passed and hash-fresh"
        elif base == "no_open_issues":
            issues = (view or {}).get("open_issues") or []
            ok = not issues
            detail = "no open issues" if ok else f"open issues {issues}"
        out.append({"name": pc, "ok": ok, "detail": detail})
    return out


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------
def _choose_variant(spec: dict, text: str, args: dict, ctx: dict):
    for name, var in (spec.get("variants") or {}).items():
        cond = var["when"]
        if cond == "always":
            return name, var
        if cond == "has_workspace" and ctx.get("exists"):
            return name, var
        head, _, rest = cond.partition(":")
        if head == "has_arg" and args.get(rest):
            return name, var
        if head == "matches" and re.search(rest, text, re.I):
            return name, var
    return None, None


def _bind(template: str, slots: dict, args: dict, required: set,
          needs: set) -> tuple[str, list[str]]:
    free: list[str] = []

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key in args and args[key] not in (None, ""):
            return str(args[key])
        if key in slots and slots[key]:
            return slots[key]
        if key in required:
            needs.add(key)
        else:
            free.append(key)
        return f"<{key}>"

    return _PLACEHOLDER.sub(sub, template), free


def build_plan(verb: str, spec: dict, tasks: dict, text: str, args: dict,
               ctx: dict, imap: dict, skill: str, gate_order: list[str],
               needs: set) -> dict:
    required = {n for n, a in (spec.get("args") or {}).items()
                if a.get("required")}
    slots = dict(ctx["slots"])
    vname, var = _choose_variant(spec, text, args, ctx)
    body = (var or spec).get("steps") or spec.get("steps") or []

    edit_class = (var or {}).get("edit_class", spec.get("edit_class"))
    if edit_class:
        ec = imap["edit_classes"][skill][edit_class]
        gates = [g for g in gate_order if g in ec["gates"]]
        stale = list(ec["stale_artifacts"])
        hold = ec["human_hold"]
        source = f"invalidation.yaml edit_classes.{skill}.{edit_class}"
    else:
        gates = [g for g in gate_order
                 if g in ((var or {}).get("gates", spec.get("gates")) or [])]
        stale = []
        hold = (var or {}).get("human_hold", spec.get("human_hold", 0))
        source = "tasks.yaml (no edit class - nothing in the block changes)"
    slots.setdefault("gate", None)
    slots["edit_class"] = edit_class

    steps: list[dict] = []
    scheduled: set[str] = set()
    for raw in body:
        step: dict = {"n": len(steps) + 1}
        for k in ("why", "when", "optional"):
            if k in raw:
                step[k] = raw[k]
        if "do" in raw:
            cmd, free = _bind(raw["do"], slots, args, required, needs)
            step.update(kind="script", command=cmd,
                        script=cmd.split()[0].split("/")[-1])
            if free:
                step["free_slots"] = sorted(set(free))
        elif "gate" in raw:
            name, free = _bind(raw["gate"], slots, args, required, needs)
            step.update(kind="gate", **_gate_step(name, slots, skill))
            scheduled.add(name)
            if free:
                step["free_slots"] = sorted(set(free))
        elif "agent" in raw:
            step.update(kind="agent", role=raw["agent"], tier=raw.get("tier"),
                        prompt=f"skills/{skill}/agents/{raw['agent']}.md")
        elif "human" in raw:
            step.update(kind="human", hold=raw["human"])
        elif "recipe" in raw:
            step.update(kind="recipe", verb=raw["recipe"],
                        summary=tasks["verbs"][raw["recipe"]]["summary"])
        else:
            step.update(kind="note",
                        note=_bind(raw.get("note", ""), slots, args, set(),
                                   set())[0])
        if "note" in raw and step.get("kind") != "note":
            step["note"] = _bind(raw["note"], slots, args, set(), set())[0]
        steps.append(step)

    for g in gates:
        if g in scheduled:
            continue
        steps.append({"n": len(steps) + 1, "kind": "gate",
                      "why": f"required by {source}", **_gate_step(g, slots, skill)})

    return {
        "verb": verb, "summary": spec["summary"], "doc": spec["doc"],
        "variant": vname, "edit_class": edit_class,
        "human_hold": hold, "human_hold_source": source,
        "gates": gates, "gates_source": source,
        "stale_artifacts": stale, "steps": steps,
    }


def _gate_step(gate: str, slots: dict, skill: str) -> dict:
    """A gate step: its command. --workspace is what RECORDS the result in
    state.json - a gate step that does not record leaves the run with a
    report on disk and no evidence in state."""
    ws = slots.get("ws", "<ws>")
    if "{" in gate:
        return {"gate": gate,
                "command": (f"scripts/gate.py --gate {gate} "
                            f"--workspace {ws}")}
    return {"gate": gate,
            "command": (f"scripts/gate.py --gate {gate} --skill {skill} "
                        f"--workspace {ws} "
                        f"--out {slots.get('reports', '<reports>')}/"
                        f"gate-{gate}.json")}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _remediations(findings: str | None) -> list[str]:
    if not findings or not Path(findings).is_file():
        return []
    try:
        import cluster_violations
        import fix_dispatch
        data = json.loads(Path(findings).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  - a bad findings file is not fatal here
        return []
    viols = data.get("failing") or data.get("violations") or []
    if isinstance(data.get("clusters"), list):
        viols = [v for c in data["clusters"] for v in c.get("violations", [])]
    kinds = {cluster_violations.kind_of(v) for v in viols if isinstance(v, dict)}
    return fix_dispatch.remediation_paths(sorted(k for k in kinds if k))


def run(argv: list[str] | None = None) -> tuple[dict, str | None]:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--skill", required=True, choices=statelib.SKILLS)
    ap.add_argument("--task", help="the request, in the caller's own words")
    ap.add_argument("--verb", help="force a verb (the LLM-classification path)")
    ap.add_argument("--workspace", help="a block workspace (may not exist yet)")
    ap.add_argument("--arg", action="append", default=[], metavar="K=V",
                    help="fill a recipe argument explicitly (repeatable)")
    ap.add_argument("--findings", help="gate result / check report for "
                                       "fix-finding (also fills its arg)")
    ap.add_argument("--list", action="store_true", help="the verb table")
    ap.add_argument("--validate", action="store_true",
                    help="registry self-check (scripts, flags, gates, classes)")
    ap.add_argument("--tasks", help="alternate engine tasks.yaml (tests)")
    ap.add_argument("--out", help="write the plan JSON here instead of stdout")
    args = ap.parse_args(argv)

    tasks = load_tasks(args.skill, args.tasks)
    imap = statelib.load_map()
    gate_order = load_gate_order(args.skill)

    if args.validate:
        problems = validate_registry(args.skill, tasks, imap)
        return ({"script": SCRIPT, "status": "planned" if not problems
                 else "error", "skill": args.skill,
                 "verbs": sorted(tasks["verbs"]),
                 "problems": problems}, args.out)

    if args.list:
        return ({"script": SCRIPT, "status": "planned", "skill": args.skill,
                 "verbs": [{"verb": v, "summary": s["summary"],
                            "workspace": s["workspace"], "doc": s["doc"],
                            "edit_class": s.get("edit_class"),
                            "args": sorted((s.get("args") or {}))}
                           for v, s in tasks["verbs"].items()]}, args.out)

    text = (args.task or "").strip()
    if not text and not args.verb:
        return ({"script": SCRIPT, "status": "unknown", "task": text,
                 "candidates": [],
                 "question": "What would you like done? (see --list for the "
                             "task types this skill routes)"}, args.out)

    candidates = match_verbs(text, tasks) if text else []
    if args.verb:
        if args.verb not in tasks["verbs"]:
            raise CheckError(f"unknown verb {args.verb!r}; --list shows them all")
        verb, how = args.verb, "forced"
    else:
        if not candidates:
            return ({"script": SCRIPT, "status": "unknown", "task": text,
                     "candidates": [],
                     "question": "No task type matched. Classify it yourself "
                                 "against --list and re-run with --verb <name>."},
                    args.out)
        if len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"]:
            tied = [c for c in candidates if c["score"] == candidates[0]["score"]]
            return ({"script": SCRIPT, "status": "ambiguous", "task": text,
                     "candidates": tied,
                     "question": "More than one task type fits. Pick one and "
                                 "re-run with --verb <name>: "
                                 + ", ".join(c["verb"] for c in tied)}, args.out)
        verb, how = candidates[0]["verb"], "table"

    spec = tasks["verbs"][verb]
    cwd = Path.cwd()
    ws = Path(args.workspace) if args.workspace else None
    ctx = workspace_context(ws, imap, args.skill)

    extracted = extract_args(text, spec, cwd) if text else {}
    explicit = {}
    for kv in args.arg:
        k, _, v = kv.partition("=")
        if not k or not v:
            raise CheckError(f"--arg must be K=V, got {kv!r}")
        explicit[k] = v
    if args.findings:
        explicit.setdefault("findings", args.findings)
    argvals = {**extracted, **explicit}
    if ctx["exists"] and (spec.get("args") or {}).keys() & {"findings", "gate"}:
        gate, report = infer_failing_gate(ctx)
        if gate:
            argvals.setdefault("gate", gate)
            if report:
                argvals.setdefault("findings", report)

    needs: set[str] = set()
    plan = build_plan(verb, spec, tasks, text, argvals, ctx, imap, args.skill,
                      gate_order, needs)
    view = resume_view(ctx)
    pres = check_preconditions(spec, ctx, view)
    plan["remediations"] = _remediations(argvals.get("findings"))

    if spec["workspace"] == "required" and not ctx["exists"]:
        needs.add("workspace")
    if not plan["steps"]:
        needs.update(n for n, a in (spec.get("args") or {}).items()
                     if a.get("extract", "only") != "none")
        needs.add("workspace")

    need_list = [{"arg": n,
                  "question": ((spec.get("args") or {}).get(n) or {}).get(
                      "question", f"Which {n}?")}
                 for n in sorted(needs)]
    if "workspace" in needs:
        for item in need_list:
            if item["arg"] == "workspace":
                item["question"] = "Which block workspace (with a state.json)?"

    blocked = [p for p in pres if not p["ok"]]
    if need_list:
        status = "needs_args"
        question = " ".join(i["question"] for i in need_list)
    elif blocked:
        status = "blocked"
        question = ("Preconditions failed: "
                    + "; ".join(f"{p['name']}: {p['detail']}" for p in blocked))
    else:
        status, question = "planned", None

    payload = {
        "script": SCRIPT, "status": status, "skill": args.skill, "task": text,
        "match": {"verb": verb, "how": how,
                  "score": next((c["score"] for c in candidates
                                 if c["verb"] == verb), None),
                  "matched": next((c["matched"] for c in candidates
                                   if c["verb"] == verb), [])},
        "candidates": candidates,
        "workspace": ctx["workspace"], "block": ctx["block"],
        "workspace_exists": ctx["exists"],
        "args": argvals, "needs": need_list,
        "preconditions": pres, "recipe": plan, "state": view,
    }
    if question:
        payload["question"] = question
    return payload, args.out


EXIT = {"planned": 0, "ambiguous": 1, "unknown": 1, "needs_args": 1,
        "blocked": 1, "error": 2}


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    try:
        payload, out = run(argv)
    except Exception as exc:  # noqa: BLE001  (any error -> exit 2)
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}, indent=1))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return EXIT.get(payload.get("status"), 2)


if __name__ == "__main__":
    raise SystemExit(main())
