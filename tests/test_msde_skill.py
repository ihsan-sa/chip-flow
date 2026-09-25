"""skills/msde/** - the skill package itself (docs/design.md, "### M10.").

Not gate behavior (test_check_split.py / test_check_cosim.py / test_check_
release.py cover that) - this is the skill's own structural contract: every
agent role a tasks.yaml step names has a role prompt with an output
contract, every finding kind the msde gates emit has a remediation
reference, the msde verbs plan cleanly in the order the nested runs need,
an interface edit cascades into both nested workspaces, and
`task_router.py --validate --skill msde` is clean."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SKILL = REPO / "skills" / "msde"
CORPUS = REPO / "corpus" / "msde"
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "lib"))

import check_split  # noqa: E402
import state as state_mod  # noqa: E402
import statelib  # noqa: E402
import task_router as tr  # noqa: E402

MSDE_ROLES = {"splitter", "integrator", "reviewer", "fixer"}
NO_WEB_ROLES = {"splitter", "integrator", "fixer", "reviewer"}
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
AGENT_OUTPUT_MARKER = re.compile(r"^## Output contract", re.M)


def msde_ws(tmp_path: Path, with_interface: bool = True) -> Path:
    ws = tmp_path / "sensor_counted"
    state_mod.State.init(ws, "msde", "sensor_counted")
    if with_interface:
        for name in ("interface.yaml", "digital_spec.yaml", "analog_spec.yaml"):
            shutil.copy2(CORPUS / "sensor_counted" / name, ws / name)
    return ws


def _record_split_pass(ws: Path) -> None:
    """integrate/cosim precondition on gates_fresh:split - fake a passing,
    hash-fresh split result rather than running the gate for a router-
    planning test."""
    st = state_mod.State.load(ws / "state.json")
    st.record_gate("split", {"status": "pass", "failing_count": 0,
                             "counts": {"total": 0}}, "P1")
    st.save()


def _steps(verb: str) -> list[dict]:
    return tr.load_tasks("msde")["verbs"][verb]["steps"]


# --------------------------------------------------------------- structure

def test_registry_validates_clean():
    assert tr.validate_registry("msde") == []


def test_skill_md_and_command_exist_and_name_the_skill():
    skill_md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert skill_md.startswith("---\nname: msde\n")
    assert "## Known limits" in skill_md
    cmd = (SKILL / "commands" / "msde.md").read_text(encoding="utf-8")
    assert "task_router.py --skill msde" in cmd


def test_every_agent_step_has_a_role_prompt_with_an_output_contract():
    tasks = tr.load_tasks("msde")
    roles = set(MSDE_ROLES)
    for spec in tasks["verbs"].values():
        bodies = [spec.get("steps") or []]
        for var in (spec.get("variants") or {}).values():
            bodies.append(var.get("steps") or [])
        for steps in bodies:
            roles |= {s["agent"] for s in steps if "agent" in s}
    # the shared engine `learn` verb's role, not docs/design.md 1.9's msde
    # roster (same exception test_vde_skill.py makes)
    roles.discard("learner")
    for role in roles:
        p = SKILL / "agents" / f"{role}.md"
        assert p.is_file(), f"missing role prompt: {p}"
        assert AGENT_OUTPUT_MARKER.search(p.read_text(encoding="utf-8")), \
            f"{p} has no '## Output contract' section"


def test_every_recipe_doc_named_in_tasks_yaml_exists():
    tasks = tr.load_tasks("msde")
    docs = {s["doc"] for s in tasks["verbs"].values() if s.get("doc")}
    assert {f"skills/msde/reference/recipes/{v}.md"
            for v in ("full-run", "split", "integrate", "cosim")} <= docs
    for doc in docs:
        assert (REPO / doc).is_file(), f"missing recipe doc: {doc}"


def test_msde_agents_have_no_web_tools_in_frontmatter():
    for role in NO_WEB_ROLES:
        p = SKILL / "agents" / f"{role}.md"
        m = FRONTMATTER_RE.match(p.read_text(encoding="utf-8"))
        assert m, f"{p} has no YAML frontmatter"
        tools = yaml.safe_load(m.group(1)).get("tools")
        assert tools, f"{p} frontmatter has no 'tools' field"
        names = {t.strip() for t in str(tools).split(",")}
        assert not names & {"WebFetch", "WebSearch"}, p


def test_integrate_mechanics_describe_top_harden_not_a_hand_join():
    """The macro and the top are top_harden's (check_top_harden.py), not an
    agent's: recipes/integrate.md and agents/integrator.md each carry one
    `## Mechanics` section that says so, names the pin-name contract and
    the clean_gds re-save - and no step anywhere tells a digital-side
    harden to take the macro."""
    for p in (SKILL / "reference" / "recipes" / "integrate.md",
              SKILL / "agents" / "integrator.md"):
        text = p.read_text(encoding="utf-8")
        assert text.count("## Mechanics") == 1, p
        body = text.split("## Mechanics", 1)[1].split("\n## ", 1)[0]
        for needle in ("top_harden", "check_top_harden", "clean_gds",
                       ".subckt", "vdd", "vss"):
            assert needle in body, (p, needle)
    for p in SKILL.rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            assert "harden_config_edit" not in text, p
            assert "--gate harden --workspace {ws}/digital" not in text, p


def test_no_skill_file_calls_a_top_gate_a_stub():
    for p in SKILL.rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            assert not re.search(r"top_(drc|lvs)`?[^.]*\bstubs?\b", text), p


def test_skill_tree_has_no_absolute_home_path():
    for p in SKILL.rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            assert "/home/" not in text, p
            assert text.isascii(), p


# ------------------------------------------------------------ remediations

COSIM_KIND_RES = (
    re.compile(r'\bbad\(\s*"([a-z_]+)"'),
    re.compile(r'checklib\.violation\(\s*"cosim",\s*"error",\s*[^,]+,\s*'
               r'[^,]+,\s*"([a-z_]+)"', re.S),
)


def _cosim_kinds() -> set[str]:
    src = (ENGINE / "scripts" / "check_cosim.py").read_text(encoding="utf-8")
    return {k for rx in COSIM_KIND_RES for k in rx.findall(src)}


TOP_KIND_RE = re.compile(r'violation\(\s*"[a-z_]+",\s*"error",\s*[^,]+,\s*'
                         r'[^,]+,\s*"([a-z_]+)"', re.S)
# top_harden passes on check_harden's findings, top_drc check_drc's
TOP_GATE_SCRIPTS = ("check_top_harden", "check_harden", "check_top_drc",
                    "check_drc", "check_top_lvs")


def _top_kinds() -> set[str]:
    kinds = set()
    for name in TOP_GATE_SCRIPTS:
        src = (ENGINE / "scripts" / f"{name}.py").read_text(encoding="utf-8")
        kinds |= set(TOP_KIND_RE.findall(src))
    return kinds


def _split_kinds() -> set[str]:
    src = (ENGINE / "scripts" / "check_split.py").read_text(encoding="utf-8")
    literal = set(re.findall(r'\bbad\(\s*"([a-z_]+)"', src))
    return literal | {f"{f}_mismatch" for f in check_split.SIGNAL_FIELDS}


def test_kind_scan_finds_the_kinds_it_should():
    """Guard the regex scan itself: if check_cosim.py's call shape changes,
    an empty scan must fail here rather than pass the test below."""
    assert {"measure_out_of_bounds", "ngspice_non_convergence", "ic_ignored",
            "test_failed", "test_skipped"} <= _cosim_kinds()
    assert {"signal_missing_from_spec", "signal_not_declared",
            "width_mismatch"} <= _split_kinds()
    assert {"macro_too_large", "flow_step_failed", "harden_missing_artifact",
            "magic_drc_violation", "klayout_drc_violation",
            "netlist_mismatch"} <= _top_kinds()


def test_every_msde_gate_kind_has_a_remediation_reference():
    rem = SKILL / "reference" / "remediations"
    kinds = (_split_kinds() | _cosim_kinds() | _top_kinds()
             | {"nested_not_released", "gate_not_ready"})
    for kind in sorted(kinds):
        assert (rem / f"{kind}.md").is_file(), \
            f"{kind}: no skills/msde/reference/remediations/{kind}.md"


def test_every_corpus_fault_kind_has_a_remediation_reference():
    rem = SKILL / "reference" / "remediations"
    kinds = set()
    for manifest in CORPUS.glob("*/faults/manifest.yaml"):
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        kinds |= {f["expect"]["kind"] for f in data["faults"]
                  if f.get("expect", {}).get("kind")}
    assert kinds, "no msde corpus fault kinds found - did the corpus move?"
    for kind in sorted(kinds):
        assert (rem / f"{kind}.md").is_file(), kind


# ------------------------------------------------------------------ verbs

def test_msde_verb_set():
    assert sorted(tr.load_tasks("msde")["verbs"]) == sorted([
        "full-run", "review", "fix-finding", "resume", "release", "learn",
        "split", "integrate", "cosim"])


def test_full_run_is_overridden_not_the_engine_placeholder():
    agents = [s["agent"] for s in _steps("full-run") if "agent" in s]
    assert agents[:1] == ["splitter"]
    assert "integrator" in agents and "reviewer" in agents


def test_full_run_gates_in_phase_order_and_never_forced():
    steps = _steps("full-run")
    gate_seq = [s["gate"] for s in steps if "gate" in s]
    # top_harden is a job: started with jobs.py, never a `gate:` step
    assert gate_seq == ["split", "cosim", "top_drc", "top_lvs", "precheck",
                        "release"]
    assert not any(s.get("gate") in ("harden", "top_harden") for s in steps)
    phases = [s["do"].rsplit(" ", 1)[1] for s in steps
              if "do" in s and "set-phase" in s["do"]]
    assert phases == ["P1", "P2", "P3", "P4"]
    assert not any("--force" in s["do"] for s in steps if "do" in s)


def test_full_run_order_both_sides_released_before_the_top_harden():
    """Each side runs to its own release in P2 - the digital side hardens
    alone, on spare TT pins - and both verify as released before P3. The
    one top_harden (a detached job in the MSDE workspace, never a gate step
    and never a harden in the digital workspace) comes after cosim, and
    top_drc/top_lvs after it."""
    steps = _steps("full-run")

    def idx(pred):
        return next(i for i, s in enumerate(steps) if pred(s))

    analog_run = idx(lambda s: "--skill ade" in s.get("do", "")
                     and "{ws}/analog" in s["do"])
    digital_run = idx(lambda s: "--skill vde" in s.get("do", "")
                      and "{ws}/digital" in s["do"])
    analog_verify = idx(lambda s: s.get("do", "").startswith(
        "scripts/attest.py verify --workspace {ws}/analog"))
    digital_verify = idx(lambda s: s.get("do", "").startswith(
        "scripts/attest.py verify --workspace {ws}/digital"))
    p3 = idx(lambda s: s.get("do", "").endswith("--phase P3"))
    integrator = idx(lambda s: s.get("agent") == "integrator")
    cosim = idx(lambda s: s.get("gate") == "cosim")
    top_harden = idx(lambda s: s.get("do", "") ==
                     "scripts/jobs.py start --gate top_harden "
                     "--workspace {ws} --skill {skill}")
    poll = idx(lambda s: s.get("do", "").startswith(
        "scripts/jobs.py status --workspace {ws} "))
    top_drc = idx(lambda s: s.get("gate") == "top_drc")
    top_lvs = idx(lambda s: s.get("gate") == "top_lvs")
    assert analog_run < analog_verify < p3
    assert digital_run < digital_verify < p3
    assert p3 < integrator < cosim < top_harden < poll < top_drc < top_lvs
    assert not any("jobs.py start --gate harden" in s.get("do", "")
                   for s in steps)
    assert not any("{ws}/digital --class harden_config_edit" in s.get("do", "")
                   for s in steps)


def test_split_plans_the_interface_cascade_into_both_nested_runs(tmp_path):
    ws = msde_ws(tmp_path)
    payload, _ = tr.run(["--skill", "msde", "--verb", "split",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    recipe = payload["recipe"]
    assert recipe["edit_class"] == "interface_edit"
    cmds = [s["command"] for s in recipe["steps"] if s["kind"] == "script"]
    for side in ("digital", "analog"):
        assert any(f"--workspace {ws}/{side} --class spec_edit" in c
                   for c in cmds), side
    # the router appends every gate interface_edit marks
    gates = [s["gate"] for s in recipe["steps"] if s["kind"] == "gate"]
    assert gates == ["split", "cosim", "top_harden", "top_drc", "top_lvs",
                     "precheck", "release"]


def test_interface_edit_still_marks_the_top_gates():
    ec = statelib.load_map()["edit_classes"]["msde"]["interface_edit"]
    assert set(["split", "cosim", "top_harden", "top_drc", "top_lvs",
                     "precheck", "release"]) <= set(ec["gates"])


def test_integrate_and_cosim_are_blocked_until_split_passes(tmp_path):
    ws = msde_ws(tmp_path)
    for verb in ("integrate", "cosim"):
        payload, _ = tr.run(["--skill", "msde", "--verb", verb,
                            "--workspace", str(ws)])
        assert payload["status"] == "blocked", verb


def test_integrate_plans_cosim_before_the_top_harden_job(tmp_path):
    ws = msde_ws(tmp_path)
    _record_split_pass(ws)
    payload, _ = tr.run(["--skill", "msde", "--verb", "integrate",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    steps = payload["recipe"]["steps"]
    cmds = [s.get("command", "") for s in steps]
    for side in ("analog", "digital"):
        assert any(f"attest.py verify --workspace {ws}/{side}" in c
                   for c in cmds), side
    cosim = next(i for i, s in enumerate(steps)
                 if s["kind"] == "gate" and s["gate"] == "cosim")
    harden = next(i for i, s in enumerate(steps) if s["kind"] == "script"
                  and f"jobs.py start --gate top_harden --workspace {ws} "
                  "--skill msde" in s["command"])
    top_drc = next(i for i, s in enumerate(steps)
                   if s["kind"] == "gate" and s["gate"] == "top_drc")
    top_lvs = next(i for i, s in enumerate(steps)
                   if s["kind"] == "gate" and s["gate"] == "top_lvs")
    assert cosim < harden < top_drc < top_lvs
    # the job is never also appended as a foreground gate step
    assert not any(s["kind"] == "gate" and s["gate"] == "top_harden"
                   for s in steps)
    assert not any(f"{ws}/digital" in c and "harden" in c for c in cmds)


def test_gate_lists_in_prose_name_every_msde_gate():
    """Every place the skill lists the msde workspace's gates lists all
    six, top_harden included."""
    msde_gates = list(tr.load_gate_order("msde"))
    assert msde_gates == ["split", "cosim", "top_harden", "top_drc", "top_lvs",
                     "precheck", "release"]
    skill_md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    full_run = (SKILL / "reference" / "recipes" / "full-run.md").read_text(
        encoding="utf-8")
    listing = "split, cosim, top_harden, top_drc, top_lvs, precheck, release"
    assert listing in skill_md
    assert listing in full_run
    for g in msde_gates:
        assert f"| {g} |" in skill_md, g


def test_cosim_plans_on_a_split_workspace(tmp_path):
    ws = msde_ws(tmp_path)
    _record_split_pass(ws)
    payload, _ = tr.run(["--skill", "msde", "--verb", "cosim",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    assert payload["recipe"]["gates"] == ["cosim"]


def test_task_words_route_to_the_msde_verbs():
    cases = {
        "design the counted sensor from corpus/msde/sensor_counted/spec.md":
            "full-run",
        "split this into digital and analog": "split",
        "integrate the analog block as a hard macro": "integrate",
        "run the co-simulation": "cosim",
    }
    for text, verb in cases.items():
        payload, _ = tr.run(["--skill", "msde", "--task", text])
        got = (payload.get("recipe") or {}).get("verb") or [
            c["verb"] for c in payload.get("candidates", [])][:1]
        assert got in (verb, [verb]), (text, payload.get("status"), got)
