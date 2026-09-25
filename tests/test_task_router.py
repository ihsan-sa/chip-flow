"""engine/scripts/task_router.py: the shared verb table (full-run, review,
fix-finding, resume, release, learn - docs/design.md 1.8), --skill merge,
matching, and planning against a real workspace fixture. The M1 done
criterion: `task_router.py --validate --skill vde` is clean."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
FIXTURES = REPO / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import pytest  # noqa: E402

import state as state_mod  # noqa: E402
import statelib  # noqa: E402
import task_router as tr  # noqa: E402

SKILLS = statelib.SKILLS


def ws_empty(tmp_path: Path) -> Path:
    dest = tmp_path / "ws-empty"
    shutil.copytree(FIXTURES / "ws-empty", dest)
    return dest


# --------------------------------------------------------- registry sanity

@pytest.mark.parametrize("skill", SKILLS)
def test_registry_validates_clean_for_every_skill(skill):
    problems = tr.validate_registry(skill)
    assert problems == [], "\n".join(problems)


def test_msde_adds_its_own_m10_verbs():
    # M10 (docs/design.md, "### M10.") merged skills/msde/reference/
    # tasks.yaml in; tests/test_msde_skill.py's test_msde_verb_set holds the
    # per-verb detail.
    tasks = tr.load_tasks("msde")
    assert sorted(tasks["verbs"]) == sorted(
        ["full-run", "review", "fix-finding", "resume", "release", "learn",
         "split", "cosim", "integrate"])


def test_vde_adds_its_own_verbs():
    # M2 landed add-test/mutate; M5 (docs/design.md, "### M5.") landed the
    # rest of section 1.8's /vde list plus a real full-run override - see
    # tests/test_vde_skill.py's test_vde_verb_set_after_m5 for the M5-era
    # assertion this one now matches.
    tasks = tr.load_tasks("vde")
    assert sorted(tasks["verbs"]) == sorted(
        ["full-run", "review", "fix-finding", "resume", "release", "learn",
         "add-test", "mutate", "spec", "prove", "harden", "fix-timing",
         "optimise"])


def test_ade_adds_its_own_m8_and_m9_verbs():
    tasks = tr.load_tasks("ade")
    assert sorted(tasks["verbs"]) == sorted(
        ["full-run", "review", "fix-finding", "resume", "release", "learn",
         "spec", "resize", "add-corner", "optimise", "layout"])


def test_gates_and_holds_are_never_restated_in_the_recipe():
    tasks = tr.load_tasks("vde")
    for verb, spec in tasks["verbs"].items():
        if spec.get("edit_class"):
            assert "gates" not in spec and "human_hold" not in spec, verb


def test_cli_validate_matches_library_call(capsys):
    code = tr.main(["--skill", "ade", "--validate"])
    assert code == 0


# ------------------------------------------------------------- skill merge

def test_skill_tasks_yaml_overrides_shared_verb(tmp_path, monkeypatch):
    """A skill's own tasks.yaml entry wins on a name collision (docs/
    design.md 1.8)."""
    skill_dir = tr.REPO / "skills" / "vde" / "reference"
    monkeypatch.setattr(tr, "_skill_tasks_path",
                        lambda skill: tmp_path / "skill-tasks.yaml"
                        if skill == "vde" else tr.REPO / "skills" / skill
                        / "reference" / "tasks.yaml")
    (tmp_path / "skill-tasks.yaml").write_text('''
verbs:
  resume:
    summary: vde-specific resume
    doc: null
    workspace: required
    edit_class: null
    gates: []
    human_hold: 0
    match: {any: ['\\bresume\\b']}
    steps:
      - note: "vde override"
''', encoding="utf-8")
    tasks = tr.load_tasks("vde")
    assert tasks["verbs"]["resume"]["summary"] == "vde-specific resume"
    # a skill that has no tasks.yaml still gets the shared table untouched
    tasks_ade = tr.load_tasks("ade")
    assert tasks_ade["verbs"]["resume"]["summary"] != "vde-specific resume"


# --------------------------------------------------------------- matching

def test_resume_task_matches_resume_verb():
    tasks = tr.load_tasks("vde")
    cands = tr.match_verbs("let's resume where we left off", tasks)
    assert cands and cands[0]["verb"] == "resume"


def test_release_task_matches_release_verb():
    tasks = tr.load_tasks("vde")
    cands = tr.match_verbs("is this ready to submit? attempt release", tasks)
    assert cands and cands[0]["verb"] == "release"


def test_no_match_is_unknown_not_a_crash():
    payload, _ = tr.run(["--skill", "vde", "--task", "xyzzy plugh"])
    assert payload["status"] == "unknown"


# --------------------------------------------------------- end-to-end plan

def test_resume_plans_on_a_real_workspace(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    payload, _ = tr.run(["--skill", "vde", "--verb", "resume",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    cmds = [s.get("command") for s in payload["recipe"]["steps"]
           if s.get("kind") == "script"]
    assert any("state.py resume" in c for c in cmds)
    assert str(ws) in cmds[0]


def test_resume_without_workspace_needs_one():
    payload, _ = tr.run(["--skill", "vde", "--verb", "resume"])
    assert payload["status"] == "needs_args"
    assert any(n["arg"] == "workspace" for n in payload["needs"])


def test_fix_finding_needs_its_required_findings_arg(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    payload, _ = tr.run(["--skill", "vde", "--verb", "fix-finding",
                        "--workspace", str(ws)])
    assert payload["status"] == "needs_args"
    assert any(n["arg"] == "findings" for n in payload["needs"])


def test_fix_finding_plans_once_findings_given(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    findings = ws / "reports" / "gate-lint.json"
    findings.parent.mkdir(parents=True, exist_ok=True)
    findings.write_text("{}", encoding="utf-8")
    payload, _ = tr.run(["--skill", "vde", "--verb", "fix-finding",
                        "--workspace", str(ws),
                        "--arg", f"findings={findings}",
                        "--arg", "gate=lint"])
    assert payload["status"] == "planned"


def test_release_gate_step_is_skill_scoped(tmp_path):
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "ade", "mirror")
    payload, _ = tr.run(["--skill", "ade", "--verb", "release",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    gate_steps = [s for s in payload["recipe"]["steps"] if s.get("kind") == "gate"]
    assert any(s["gate"] == "release" for s in gate_steps)
    assert "--skill ade" in gate_steps[0]["command"]


def test_resume_view_and_no_open_issues_precondition_treat_escalated_as_open():
    """Same rule as state.py's own resume_summary and attest.py's build()
    (M5, found running the fix loop for real): a `no_open_issues`
    precondition must not wave an escalated (human-decision-pending) issue
    through as if it were closed - only fixed/waived are genuinely done."""
    data = {"open_issues": [
        {"id": 1, "status": "escalated"},
        {"id": 2, "status": "fixed"},
        {"id": 3, "status": "waived"},
        {"id": 4, "status": "open"},
    ], "gates": {}}
    ctx = {"workspace": ".", "state": data}
    view = tr.resume_view(ctx)
    assert sorted(view["open_issues"]) == [1, 4]
    problems = tr.check_preconditions({"preconditions": ["no_open_issues"]},
                                      {"exists": True}, view)
    assert problems[0]["name"] == "no_open_issues"
    assert problems[0]["ok"] is False


def test_list_shows_every_msde_verb():
    payload, _ = tr.run(["--skill", "msde", "--list"])
    assert {v["verb"] for v in payload["verbs"]} == {
        "full-run", "review", "fix-finding", "resume", "release", "learn",
        "split", "cosim", "integrate"}


# --------------------------------------------------- CHIP_FLOW_HOME binding

def test_render_binds_scripts_to_chip_flow_home_from_a_different_cwd(
        tmp_path, monkeypatch):
    """docs/design.md 1.1: a skill reaches the engine only through
    $CHIP_FLOW_HOME, never a path relative to cwd or a repo checkout - a
    read-only skill checkout has no `engine/` sibling to resolve
    `scripts/<name>.py` against. Prove a rendered plan's own commands
    resolve to real files even when this process's cwd is nowhere near the
    repo."""
    monkeypatch.setenv("CHIP_FLOW_HOME", str(REPO))
    monkeypatch.chdir(tmp_path)
    ws = ws_empty(tmp_path)
    state_mod.State.init(ws, "vde", "counter8")
    payload, _ = tr.run(["--skill", "vde", "--verb", "resume",
                        "--workspace", str(ws)])
    assert payload["status"] == "planned"
    cmds = [s["command"] for s in payload["recipe"]["steps"]
           if s.get("kind") == "script"]
    assert cmds, "resume should render at least one script step"
    prefix = str(REPO / "engine" / "scripts") + "/"
    for c in cmds:
        assert c.startswith(prefix), c
        script_path = Path(c.split()[0])
        assert script_path.is_file(), f"rendered command names a script " \
                                      f"that does not exist: {script_path}"


def test_chip_flow_home_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("CHIP_FLOW_HOME", raising=False)
    assert tr.chip_flow_home() == Path(
        "~/.claude/skills/chip-flow").expanduser()
