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


def test_integrate_mechanics_is_the_one_marked_placeholder():
    """The integrate mechanics come from a spike; recipes/integrate.md and
    agents/integrator.md each carry exactly one `## Mechanics` section
    holding only the pointer, so filling it in is one edit per file."""
    for p in (SKILL / "reference" / "recipes" / "integrate.md",
              SKILL / "agents" / "integrator.md"):
        text = p.read_text(encoding="utf-8")
        assert text.count("## Mechanics") == 1, p
        body = text.split("## Mechanics", 1)[1].split("\n## ", 1)[0]
        assert body.strip() == "See docs/spikes/macro_harden.md.", p


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


def test_every_msde_gate_kind_has_a_remediation_reference():
    rem = SKILL / "reference" / "remediations"
    kinds = _split_kinds() | _cosim_kinds() | {"nested_not_released",
                                                "gate_not_ready"}
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
    assert gate_seq == ["split", "cosim", "top_drc", "top_lvs", "release"]
    phases = [s["do"].rsplit(" ", 1)[1] for s in steps
              if "do" in s and "set-phase" in s["do"]]
    assert phases == ["P1", "P2", "P3", "P4"]
    assert not any("--force" in s["do"] for s in steps if "do" in s)


def test_full_run_order_analog_released_before_the_digital_harden():
    """The analog GDS is the digital side's hard macro: the analog nested
    run and its release check come before the integrator, and the one
    digital harden (a detached job in the DIGITAL workspace, never a
    synchronous gate step) comes after it; the top gates come after that."""
    steps = _steps("full-run")

    def idx(pred):
        return next(i for i, s in enumerate(steps) if pred(s))

    analog_run = idx(lambda s: "--skill ade" in s.get("do", "")
                     and "{ws}/analog" in s["do"])
    digital_run = idx(lambda s: "--skill vde" in s.get("do", "")
                      and "{ws}/digital" in s["do"])
    analog_verify = idx(lambda s: s.get("do", "").startswith(
        "scripts/attest.py verify --workspace {ws}/analog"))
    integrator = idx(lambda s: s.get("agent") == "integrator")
    harden = idx(lambda s: s.get("do", "").startswith(
        "scripts/jobs.py start --gate harden --workspace {ws}/digital"))
    digital_verify = idx(lambda s: s.get("do", "").startswith(
        "scripts/attest.py verify --workspace {ws}/digital"))
    top_drc = idx(lambda s: s.get("gate") == "top_drc")
    assert analog_run < analog_verify < integrator < harden
    assert digital_run < integrator
    assert harden < digital_verify < top_drc
    assert not any(s.get("gate") == "harden" for s in steps)


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
    assert gates == ["split", "cosim", "top_drc", "top_lvs", "release"]


def test_interface_edit_still_marks_the_top_gates():
    ec = statelib.load_map()["edit_classes"]["msde"]["interface_edit"]
    assert {"split", "cosim", "top_drc", "top_lvs", "release"} <= set(ec["gates"])


def test_integrate_and_cosim_are_blocked_until_split_passes(tmp_path):
    ws = msde_ws(tmp_path)
    for verb in ("integrate", "cosim"):
        payload, _ = tr.run(["--skill", "msde", "--verb", verb,
                            "--workspace", str(ws)])
        assert payload["status"] == "blocked", verb


def test_integrate_plans_cosim_before_the_digital_harden(tmp_path):
    ws = msde_ws(tmp_path)
    _record_split_pass(ws)
    payload, _ = tr.run(["--skill", "msde", "--verb", "integrate",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    steps = payload["recipe"]["steps"]
    cosim = next(i for i, s in enumerate(steps)
                 if s["kind"] == "gate" and s["gate"] == "cosim")
    harden = next(i for i, s in enumerate(steps) if s["kind"] == "script"
                  and f"jobs.py start --gate harden --workspace {ws}/digital"
                  in s["command"])
    top_lvs = next(i for i, s in enumerate(steps)
                   if s["kind"] == "gate" and s["gate"] == "top_lvs")
    assert cosim < harden < top_lvs


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
