"""skills/vde/** - the skill package itself (docs/design.md, "### M5.").

Not gate behavior (check_*.py's own tests cover that) - this is the skill's
own structural contract: every agent role a tasks.yaml step names actually
has a role prompt with an output contract, every corpus fault kind has
routing AND a remediation reference, the new M5 verbs plan cleanly, and
`task_router.py --validate --skill vde` (the M5 done criterion) is clean."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SKILL = REPO / "skills" / "vde"
CORPUS = REPO / "corpus" / "vde"
FIXTURES = REPO / "tests" / "fixtures"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))

import cluster_violations  # noqa: E402
import state as state_mod  # noqa: E402
import task_router as tr  # noqa: E402


def ws_empty(tmp_path: Path) -> Path:
    dest = tmp_path / "ws-empty"
    shutil.copytree(FIXTURES / "ws-empty", dest)
    return dest


def _record_synth_pass(ws: Path) -> None:
    """harden/optimise both precondition on gates_fresh:synth - fake a
    passing, hash-fresh synth result the cheap way rather than running the
    real toolchain for a router-planning test."""
    st = state_mod.State.load(ws / "state.json")
    st.record_gate("synth", {"status": "pass", "failing_count": 0,
                             "counts": {"total": 0}}, "P5")
    st.save()


# --------------------------------------------------------------- structure

def test_registry_validates_clean():
    """The M5 done criterion, as a unit test."""
    assert tr.validate_registry("vde") == []


def test_skill_md_and_command_exist_and_name_the_skill():
    skill_md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert skill_md.startswith("---\nname: vde\n")
    cmd = (SKILL / "commands" / "vde.md").read_text(encoding="utf-8")
    assert "task_router.py --skill vde" in cmd


AGENT_OUTPUT_MARKER = re.compile(r"^## Output contract", re.M)


def test_every_agent_step_has_a_role_prompt_with_an_output_contract():
    tasks = tr.load_tasks("vde")
    roles = set()
    for spec in tasks["verbs"].values():
        bodies = [spec.get("steps") or []]
        for var in (spec.get("variants") or {}).values():
            bodies.append(var.get("steps") or [])
        for steps in bodies:
            for step in steps:
                if "agent" in step:
                    roles.add(step["agent"])
    # every role docs/design.md 1.9 names for /vde, whether or not a
    # current verb spawns it yet (optimiser: M7 placeholder, SKILL.md's
    # own spawn-tier table still lists it)
    roles |= {"spec-writer", "architect", "tb-writer", "property-writer",
              "rtl-writer", "reviewer", "fixer", "optimiser"}
    # "learner" comes from the SHARED engine `learn` verb (engine/
    # reference/tasks.yaml), not from docs/design.md 1.9's per-skill vde
    # roster - a teaching-session role prompt is shared-engine/other-
    # milestone scope, not this one's.
    roles.discard("learner")
    assert roles, "no agent roles found - did load_tasks break?"
    for role in roles:
        p = SKILL / "agents" / f"{role}.md"
        assert p.is_file(), f"missing role prompt: {p}"
        text = p.read_text(encoding="utf-8")
        assert AGENT_OUTPUT_MARKER.search(text), \
            f"{p} has no '## Output contract' section"


def test_every_recipe_doc_named_in_tasks_yaml_exists():
    tasks = tr.load_tasks("vde")
    docs = [s["doc"] for s in tasks["verbs"].values() if s.get("doc")]
    assert docs, "expected at least one non-null recipe doc at M5"
    for doc in docs:
        assert (REPO / doc).is_file(), f"missing recipe doc: {doc}"


DESIGN_AND_FIXER_AGENTS = {"spec-writer", "architect", "tb-writer",
                           "property-writer", "rtl-writer", "fixer"}
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def test_design_and_fixer_agents_have_no_web_tools_in_frontmatter():
    """docs/design.md 1.9: "Design and fixer agents get no web tools" -
    enforced via the Claude Code agent `tools:` frontmatter field, not just
    asked for in prose. An agent role prompt with no `tools:` field at all
    inherits every tool, WebFetch/WebSearch included - a bare "No web
    tools." sentence in the body is a request, not an enforcement."""
    for role in DESIGN_AND_FIXER_AGENTS:
        p = SKILL / "agents" / f"{role}.md"
        text = p.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(text)
        assert m, f"{p} has no YAML frontmatter"
        front = yaml.safe_load(m.group(1))
        tools = front.get("tools")
        assert tools, f"{p} frontmatter has no 'tools' field"
        tool_names = {t.strip() for t in str(tools).split(",")}
        assert "WebFetch" not in tool_names, p
        assert "WebSearch" not in tool_names, p


def test_templates_dir_is_not_empty():
    files = list((SKILL / "templates").iterdir())
    assert files, "skills/vde/templates/ has nothing in it"


# ------------------------------------------------------- fixer-domain routing

def _manifest_kinds() -> set[str]:
    kinds = set()
    for rung in CORPUS.iterdir():
        manifest = rung / "faults" / "manifest.yaml"
        if not manifest.is_file():
            continue
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        for fault in data["faults"]:
            k = fault.get("expect", {}).get("kind")
            if k:
                kinds.add(k)
    return kinds


def test_every_corpus_fault_kind_routes_to_a_real_fixer_domain():
    """Every kind the corpus's own faults/manifest.yaml files exercise must
    resolve to a domain fix_dispatch.py actually knows how to dispatch -
    the mutate/tb-writer-not-rtl-writer rule (docs/design.md section 2) is
    exactly the kind of thing a silent 'review' fallback would hide."""
    # requirement_no_check (spec_lint) is the one deliberate exception: a
    # spec_lint failure has no rtl/tb/formal file for a fixer domain to
    # edit, so it is handled directly by the spec-writer/architect inside
    # the `spec`/`full-run` recipes, never through fix_dispatch's generic
    # domain routing (skills/vde/reference/remediations/
    # requirement_no_check.md explains this explicitly).
    no_fixer_domain = {"requirement_no_check"}
    kinds = _manifest_kinds()
    assert kinds, "no corpus fault kinds found - did the corpus move?"
    for kind in sorted(kinds - no_fixer_domain):
        domain = cluster_violations.FIXER_HINTS.get(kind)
        assert domain is not None, f"{kind}: no FIXER_HINTS entry"
        assert domain in cluster_violations.FIXER_DOMAINS
    assert no_fixer_domain <= kinds, \
        "the deliberate exception no longer appears in the corpus - remove it"


def test_mutate_and_holdout_kinds_never_route_to_rtl_writer_by_accident():
    """docs/design.md section 2: a mutate failure goes back to the
    tb-writer, never the rtl-writer."""
    assert cluster_violations.FIXER_HINTS["kill_rate_below_threshold"] == "testbench"
    for cls in ("reset_removed", "output_stuck", "condition_inverted"):
        assert cluster_violations.FIXER_HINTS[f"survivor_{cls}"] == "testbench"


def test_every_corpus_fault_kind_has_a_remediation_reference():
    rem_dir = SKILL / "reference" / "remediations"
    for kind in sorted(_manifest_kinds()):
        assert (rem_dir / f"{kind}.md").is_file(), \
            f"{kind}: no skills/vde/reference/remediations/{kind}.md"


# --------------------------------------------------------------- new verbs

def test_vde_verb_set_after_m5():
    tasks = tr.load_tasks("vde")
    assert sorted(tasks["verbs"]) == sorted([
        "full-run", "review", "fix-finding", "resume", "release", "learn",
        "add-test", "mutate", "spec", "prove", "harden", "fix-timing",
        "optimise"])


def test_full_run_is_overridden_not_the_engine_placeholder():
    tasks = tr.load_tasks("vde")
    steps = tasks["verbs"]["full-run"]["steps"]
    agents = [s["agent"] for s in steps if "agent" in s]
    assert agents[:1] == ["spec-writer"]
    assert "rtl-writer" in agents
    assert "reviewer" in agents


def test_full_run_never_forces_past_p6_signoff_gates():
    """set-phase --force skips gate_coverage's own check that every gate
    owed before the target phase has a recorded result - using it to reach
    P8 would waive every P6 signoff gate (harden/timing/drc/lvs/glsim/
    precheck) with no recorded reason or approval. full-run must reach P8
    through P7 (which owes no gate of its own) with plain set-phase calls
    only."""
    tasks = tr.load_tasks("vde")
    steps = tasks["verbs"]["full-run"]["steps"]
    do_cmds = [s["do"] for s in steps if "do" in s]
    set_phase_cmds = [c for c in do_cmds if "set-phase" in c]
    assert not any("--force" in c for c in set_phase_cmds), set_phase_cmds
    assert any(c.endswith("--phase P7") for c in set_phase_cmds)
    assert any(c.endswith("--phase P8") for c in set_phase_cmds)


def test_full_run_plans_p1_through_h2_in_gate_phase_order():
    ws = ws_empty  # noqa: F841 - not used; full-run creates its own workspace
    tasks = tr.load_tasks("vde")
    steps = tasks["verbs"]["full-run"]["steps"]
    gate_seq = [s["gate"] for s in steps if "gate" in s]
    # every P4 gate appears, in gates.yaml's own declared order, and P5
    # synth comes after all of them
    p4_order = ["lint", "sim", "holdout", "mutate", "formal", "cover"]
    p4_positions = [gate_seq.index(g) for g in p4_order]
    assert p4_positions == sorted(p4_positions)
    assert gate_seq.index("synth") > max(p4_positions)
    assert gate_seq.index("release") == len(gate_seq) - 1


def test_prove_plans_on_a_real_workspace(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    payload, _ = tr.run(["--skill", "vde", "--verb", "prove",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    gate_steps = [s for s in payload["recipe"]["steps"] if s.get("kind") == "gate"]
    assert any(s["gate"] == "formal" for s in gate_steps)


def test_harden_plans_a_job_start_not_a_synchronous_gate(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    _record_synth_pass(ws)
    payload, _ = tr.run(["--skill", "vde", "--verb", "harden",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    cmds = [s.get("command") for s in payload["recipe"]["steps"]
           if s.get("kind") == "script"]
    assert any("jobs.py start --gate harden" in c for c in cmds)
    # never a literal synchronous `gate: harden` step - that would block
    # on a job that can run the better part of an hour
    assert not any(s.get("kind") == "gate" and s.get("gate") == "harden"
                  for s in payload["recipe"]["steps"])


def test_fix_timing_needs_findings_and_edit_class(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    payload, _ = tr.run(["--skill", "vde", "--verb", "fix-timing",
                        "--workspace", str(ws)])
    assert payload["status"] == "needs_args"
    needed = {n["arg"] for n in payload["needs"]}
    assert {"findings", "edit_class"} <= needed


def test_fix_timing_plans_once_args_given_and_reharden_precedes_timing(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    findings = ws / "reports" / "gate-timing.json"
    findings.parent.mkdir(parents=True, exist_ok=True)
    findings.write_text("{}", encoding="utf-8")
    payload, _ = tr.run(["--skill", "vde", "--verb", "fix-timing",
                        "--workspace", str(ws),
                        "--arg", f"findings={findings}",
                        "--arg", "edit_class=rtl_edit"])
    assert payload["status"] == "planned"
    steps = payload["recipe"]["steps"]
    harden_idx = next(i for i, s in enumerate(steps)
                      if s.get("kind") == "script"
                      and "jobs.py start --gate harden" in s.get("command", ""))
    timing_idx = next(i for i, s in enumerate(steps)
                      if s.get("kind") == "gate" and s["gate"] == "timing")
    assert harden_idx < timing_idx


def test_optimise_never_calls_a_script_that_does_not_exist_yet(tmp_path):
    """optimise.py is M7's build - this verb's steps must resolve to real,
    already-existing scripts only (task_router.py --validate already
    proves this structurally; this test proves the planned commands agree
    at runtime too)."""
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    _record_synth_pass(ws)
    payload, _ = tr.run(["--skill", "vde", "--verb", "optimise",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    for step in payload["recipe"]["steps"]:
        if step.get("kind") == "script":
            assert step["script"] != "optimise.py"
