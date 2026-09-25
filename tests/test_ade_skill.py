"""skills/ade/** - the skill package itself (docs/design.md, "### M9.").

Not gate behavior (check_*.py's own tests cover that) - this is the skill's
own structural contract: every agent role a tasks.yaml step (or
fix_dispatch's ROLE_BY_DOMAIN routing) names actually has a role prompt
with an output contract, every kind the ade check scripts can emit
resolves to a real remediation reference, the verbs plan cleanly, and
`task_router.py --validate --skill ade` is clean."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SKILL = REPO / "skills" / "ade"
CORPUS = REPO / "corpus" / "ade"
FIXTURES = REPO / "tests" / "fixtures"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))

import cluster_violations  # noqa: E402
import fix_dispatch  # noqa: E402
import state as state_mod  # noqa: E402
import task_router as tr  # noqa: E402


def ws_empty(tmp_path: Path) -> Path:
    dest = tmp_path / "ws-empty"
    shutil.copytree(FIXTURES / "ws-empty", dest)
    return dest


# --------------------------------------------------------------- structure

def test_registry_validates_clean():
    assert tr.validate_registry("ade") == []


def test_skill_md_and_command_exist_and_name_the_skill():
    skill_md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert skill_md.startswith("---\nname: ade\n")
    cmd = (SKILL / "commands" / "ade.md").read_text(encoding="utf-8")
    assert "task_router.py --skill ade" in cmd


AGENT_OUTPUT_MARKER = re.compile(r"^## Output contract", re.M)

# Every role SKILL.md's own spawn-tier table (1.9-equivalent section) and
# fix_dispatch.ROLE_BY_DOMAIN["ade"] name, whether or not a current verb's
# tasks.yaml step spawns it yet: spec-writer/analog-designer/bench-writer/
# reviewer/fixer from the P1-P6 steps, layout-writer/layout-fixer from P5
# and the layout fix loop, optimiser from the spawn-tier table (M9's
# `optimise` verb runs optimise.py directly; an agent may still append
# trials per the recipe doc, same placeholder shape as /vde's optimiser).
ADE_ROLES = {"spec-writer", "analog-designer", "bench-writer",
             "layout-writer", "layout-fixer", "reviewer", "fixer",
             "optimiser"}


def test_every_agent_step_has_a_role_prompt_with_an_output_contract():
    tasks = tr.load_tasks("ade")
    roles = set()
    for spec in tasks["verbs"].values():
        bodies = [spec.get("steps") or []]
        for var in (spec.get("variants") or {}).values():
            bodies.append(var.get("steps") or [])
        for steps in bodies:
            for step in steps:
                if "agent" in step:
                    roles.add(step["agent"])
    # "learner" comes from the SHARED engine `learn` verb (engine/
    # reference/tasks.yaml), not from /ade's own roster.
    roles.discard("learner")
    assert roles, "no agent roles found - did load_tasks break?"
    roles |= ADE_ROLES
    for role in roles:
        p = SKILL / "agents" / f"{role}.md"
        assert p.is_file(), f"missing role prompt: {p}"
        text = p.read_text(encoding="utf-8")
        assert AGENT_OUTPUT_MARKER.search(text), \
            f"{p} has no '## Output contract' section"


def test_every_fix_dispatch_role_by_domain_role_has_a_prompt():
    """fix_dispatch.py's ROLE_BY_DOMAIN["ade"] routes layout/testbench
    findings to layout-fixer/bench-writer directly - neither role is named
    by an `agent:` step in tasks.yaml (they're dispatched, not scripted),
    so the structural check above would miss a typo'd role name here."""
    for domain, role in fix_dispatch.ROLE_BY_DOMAIN["ade"].items():
        assert domain in cluster_violations.FIXER_DOMAINS
        p = SKILL / "agents" / f"{role}.md"
        assert p.is_file(), f"missing role prompt: {p}"


def test_every_recipe_doc_named_in_tasks_yaml_exists():
    tasks = tr.load_tasks("ade")
    docs = [s["doc"] for s in tasks["verbs"].values() if s.get("doc")]
    assert docs, "expected at least one non-null recipe doc"
    for doc in docs:
        assert (REPO / doc).is_file(), f"missing recipe doc: {doc}"


# every /ade role is a design or fixer role (docs/design.md 1.9: "Design
# and fixer agents get no web tools") - reviewer included, since it is
# always fresh-context and never a design/fixer conversation but still
# gets no web tools per SKILL.md's own non-negotiable rule 3.
DESIGN_AND_FIXER_AGENTS = set(ADE_ROLES)
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def test_design_and_fixer_agents_have_no_web_tools_in_frontmatter():
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
    assert files, "skills/ade/templates/ has nothing in it"


def test_spec_yaml_template_parses_and_has_the_ade_schema_keys():
    text = (SKILL / "templates" / "spec.yaml.template").read_text(encoding="utf-8")
    # strip comment-only/blank lines the way a human editing the template
    # would leave them - what's left must be valid YAML with the keys
    # lint_spec_ade requires or documents.
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    data = yaml.safe_load("\n".join(lines))
    assert isinstance(data, dict)
    for key in ("top", "supply", "devices", "measures"):
        assert key in data, f"spec.yaml.template missing {key!r}"
    assert isinstance(data["measures"], list) and data["measures"]
    assert "bounds" in data["measures"][0]


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
    """gate_not_ready is the one deliberate exception, same shape as vde's
    requirement_no_check: attest.py's own coverage refusal has no rtl/tb/
    netlist file for a fixer domain to edit, so it is handled directly by
    a human/orchestrator triage (review), never fix_dispatch's generic
    domain routing (skills/ade/reference/remediations/gate_not_ready.md
    says so explicitly)."""
    no_fixer_domain = {"gate_not_ready"}
    kinds = _manifest_kinds()
    assert kinds, "no corpus fault kinds found - did the corpus move?"
    for kind in sorted(kinds - no_fixer_domain):
        domain = cluster_violations.fixer_for(kind, checks=())
        # an open-set DRC rule name (M1.2a, DF.14_LV, metal1_OFFGRID, ...)
        # has no FIXER_HINTS entry of its own - it falls back to
        # CHECK_HINTS by the finding's `check` name (analog_drc -> layout);
        # fixer_for alone (no checks passed) can't see that, so check the
        # two real routing tables cluster_violations actually offers.
        if domain == "review" and kind not in cluster_violations.FIXER_HINTS:
            domain = cluster_violations.CHECK_HINTS.get("analog_drc")
        assert domain in cluster_violations.FIXER_DOMAINS, kind
        assert domain != "review", f"{kind}: falls back to review"


def test_bench_strength_survivors_never_route_to_the_designer():
    """docs/design.md section 2, analog form: a bench_strength survivor is
    the BENCH's fault - every survivor_* class routes to testbench (the
    bench-writer), never sizing/netlist (the analog-designer)."""
    for cls in ("size_doubled", "connection_removed", "type_flipped",
               "bias_halved"):
        assert cluster_violations.FIXER_HINTS[f"survivor_{cls}"] == "testbench"


def test_every_corpus_fault_kind_has_a_remediation_reference():
    """Uses fix_dispatch.remediation_ref's own resolution order (exact
    kind, then family, then check name) rather than an exact-filename
    check: a klayout DRC rule name (M1.2a, DF.14_LV, metal1_OFFGRID) is an
    open set with no per-rule file, by design (SKILL.md: "every klayout
    DRC rule lands on analog_drc.md") - that IS the reference, resolved
    through the check name, not a missing one."""
    rem_dir = SKILL / "reference" / "remediations"
    manifest = {}
    for rung in CORPUS.iterdir():
        mpath = rung / "faults" / "manifest.yaml"
        if not mpath.is_file():
            continue
        data = yaml.safe_load(mpath.read_text(encoding="utf-8"))
        for fault in data["faults"]:
            k = fault.get("expect", {}).get("kind")
            gate = fault.get("gate")
            if k:
                manifest.setdefault(k, set()).add(gate)
    assert manifest, "no corpus fault kinds found - did the corpus move?"
    gate_to_check = {"drc": "analog_drc", "lvs": "analog_lvs",
                     "pex_sim": "pex_sim"}
    for kind, gates in sorted(manifest.items()):
        checks = {gate_to_check[g] for g in gates if g in gate_to_check}
        ref = fix_dispatch.remediation_ref(kind, rem_dir, checks)
        assert ref is not None, \
            f"{kind}: no remediation resolves (kind/family/check exhausted)"


def test_release_gate_not_ready_has_its_own_remediation():
    """release's own kind (attest.py's coverage refusal) is not in any
    corpus manifest (it fires on a block that skipped a gate, not a
    planted fault a manifest demonstrates) - check it directly."""
    assert (SKILL / "reference" / "remediations" / "gate_not_ready.md").is_file()


# --------------------------------------------------------------- verb set

def test_ade_verb_set():
    tasks = tr.load_tasks("ade")
    assert sorted(tasks["verbs"]) == sorted([
        "full-run", "spec", "resize", "add-corner", "layout", "optimise",
        "review", "fix-finding", "resume", "release", "learn"])


def test_full_run_is_overridden_not_the_engine_placeholder():
    tasks = tr.load_tasks("ade")
    steps = tasks["verbs"]["full-run"]["steps"]
    agents = [s["agent"] for s in steps if "agent" in s]
    assert agents[:1] == ["spec-writer"]
    assert "layout-writer" in agents
    assert "reviewer" in agents


def test_full_run_plans_p4_gates_in_gates_yaml_order_then_layout_then_release():
    tasks = tr.load_tasks("ade")
    steps = tasks["verbs"]["full-run"]["steps"]
    gate_seq = [s["gate"] for s in steps if "gate" in s]
    p4_order = ["netlist_lint", "sim_tt", "sim_pvt", "bench_strength", "mc"]
    p4_positions = [gate_seq.index(g) for g in p4_order]
    assert p4_positions == sorted(p4_positions)
    p5_order = ["drc", "lvs", "pex_sim"]
    p5_positions = [gate_seq.index(g) for g in p5_order]
    assert p5_positions == sorted(p5_positions)
    assert min(p5_positions) > max(p4_positions)
    assert gate_seq.index("release") == len(gate_seq) - 1


def test_full_run_never_forces_past_p6_release():
    tasks = tr.load_tasks("ade")
    steps = tasks["verbs"]["full-run"]["steps"]
    do_cmds = [s["do"] for s in steps if "do" in s]
    set_phase_cmds = [c for c in do_cmds if "set-phase" in c]
    assert not any("--force" in c for c in set_phase_cmds), set_phase_cmds
    assert any(c.endswith("--phase P6") for c in set_phase_cmds)


def test_resize_plans_on_a_real_workspace(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "mirror")
    payload, _ = tr.run(["--skill", "ade", "--verb", "resize",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    gate_steps = [s for s in payload["recipe"]["steps"] if s.get("kind") == "gate"]
    gate_names = [s["gate"] for s in gate_steps]
    # docs/design.md 5 / recipes/resize.md: never sim_tt alone
    assert "sim_tt" in gate_names and "sim_pvt" in gate_names
    assert gate_names.index("sim_tt") < gate_names.index("sim_pvt")


def test_add_corner_needs_the_corner_arg(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "mirror")
    payload, _ = tr.run(["--skill", "ade", "--verb", "add-corner",
                        "--workspace", str(ws)])
    assert payload["status"] == "needs_args"
    assert {"corner"} <= {n["arg"] for n in payload["needs"]}


def test_layout_plans_drc_lvs_pex_sim_in_order(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "mirror")
    payload, _ = tr.run(["--skill", "ade", "--verb", "layout",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    gate_names = [s["gate"] for s in payload["recipe"]["steps"]
                 if s.get("kind") == "gate"]
    assert gate_names.index("drc") < gate_names.index("lvs") < gate_names.index("pex_sim")
    agents = [s["role"] for s in payload["recipe"]["steps"] if s.get("kind") == "agent"]
    assert agents == ["layout-writer"]


def test_optimise_only_calls_scripts_that_already_exist(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "mirror")
    payload, _ = tr.run(["--skill", "ade", "--verb", "optimise",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    for step in payload["recipe"]["steps"]:
        if step.get("kind") == "script":
            script = (ENGINE / "scripts" / step["script"])
            assert script.is_file(), step["script"]
