"""evals/round/: the sandboxed eval-round runner. The limit decisions with
fakes, the bwrap argv, the skill export, the stand-in approval and the one
notice; one slow test runs the isolation probe in a real sandbox."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evals" / "round"))

import pytest  # noqa: E402

import round as rnd  # noqa: E402
import sandbox as sb  # noqa: E402

CRED_TEXT = '{"claudeAiOauth": {"refreshToken": "FIXTURE-SECRET-xyz"}}'


# ------------------------------------------------------------- fixtures
def _claude_dir(root: Path) -> Path:
    """A stand-in ~/.claude holding one of each kind of entry."""
    cd = root / ".claude"
    for d in ("projects", "skills", "agents", "plugins", "session-env"):
        (cd / d).mkdir(parents=True)
    (cd / "projects" / "-home-x").mkdir()
    (cd / "projects" / "-home-x" / "old.jsonl").write_text("{}\n")
    (cd / "skills" / "other").mkdir()
    (cd / sb.CREDENTIALS).write_text(CRED_TEXT)
    (cd / "settings.json").write_text('{"hooks": {"Stop": []}}')
    (cd / "CLAUDE.md").write_text("private instructions\n")
    (cd / "history.jsonl").write_text('{"display": "x"}\n')
    return cd


def _claude_json(root: Path) -> Path:
    p = root / ".claude.json"
    p.write_text(json.dumps({
        "oauthAccount": {"accountUuid": "u", "emailAddress": "a@b.invalid"},
        "userID": "id", "projects": {"/somewhere": {}},
        "mcpServers": {"x": {}}}))
    return p


def _layout(tmp: Path, arm: str, run: str = "run1",
            export: Path | None = None) -> sb.Layout:
    home = tmp / "realhome"
    if not (home / ".claude").exists():
        _claude_dir(home)
        _claude_json(home)
    rd = tmp / "round" / run
    sb.prepare_run(rd, "# spec\n", real_claude_json=home / ".claude.json")
    (tmp / "edatree").mkdir(exist_ok=True)
    (tmp / "edabin").mkdir(exist_ok=True)
    (tmp / "claude-elf").write_text("")
    return sb.Layout(arm=arm, run_dir=rd, claude_bin=tmp / "claude-elf",
                     eda_tree=tmp / "edatree", eda_bin_dir=tmp / "edabin",
                     real_claude_dir=home / ".claude", export_dir=export)


def _mounts(argv: list[str]) -> dict[str, tuple[str, str | None]]:
    """target -> (op, source) for the bwrap mount options."""
    out, i = {}, 0
    while i < len(argv) and argv[i] != "--":
        a = argv[i]
        if a in ("--bind", "--ro-bind", "--symlink", "--setenv"):
            out[argv[i + 2]] = (a, argv[i + 1])
            i += 3
        elif a in ("--tmpfs", "--proc", "--dev", "--chdir", "--hostname"):
            out[argv[i + 1]] = (a, None)
            i += 2
        else:
            i += 1
    return out


# ----------------------------------------------------- limit decisions
def test_budget_decision_caps_the_total_and_the_run():
    assert rnd.budget_decision(0.0) == {"ok": True, "spent": 0.0,
                                        "run_budget": 70.0}
    assert rnd.budget_decision(430.0)["ok"]
    assert rnd.budget_decision(430.0)["run_budget"] == 70.0
    no = rnd.budget_decision(430.01)
    assert not no["ok"] and "budget" in no["reason"]


def test_usage_limit_decision():
    assert not rnd.usage_limit_decision(lambda: (0, "usage limit until 10:00Z"))["ok"]
    assert rnd.usage_limit_decision(lambda: (1, "clear"))["ok"]
    assert not rnd.usage_limit_decision(lambda: (2, "?"))["ok"]

    def boom():
        raise OSError("no cc-limit")
    assert not rnd.usage_limit_decision(boom)["ok"]


def test_wait_for_load_sleeps_until_the_load_drops():
    loads, slept = iter([15.0, 12.5, 4.0]), []
    r = rnd.wait_for_load(lambda: next(loads), slept.append)
    assert r["ok"] and r["load"] == 4.0
    assert slept == [rnd.LOAD_SLEEP_S, rnd.LOAD_SLEEP_S]
    slept.clear()
    r = rnd.wait_for_load(lambda: 30.0, slept.append, dry_run=True)
    assert r["ok"] and slept == [] and "would sleep" in r["note"]


def test_overrun_decision():
    assert rnd.overrun_decision(70.0)["ok"]
    assert not rnd.overrun_decision(70.01)["ok"]


# ------------------------------------------------------ the round loop
class Fake:
    """A Round with every outside effect stubbed: run_cell returns a run
    of the cost `costs` gives, notify is counted."""

    def __init__(self, tmp: Path, costs=None, status=(1, "clear"),
                 loads=None, raise_on=None, dry_run=False):
        self.calls, self.notices, self.slept = [], [], []
        loads = iter(loads or [1.0] * 100)
        self.r = rnd.Round(tmp / "round", dry_run=dry_run,
                           status_fn=lambda: status,
                           load_fn=lambda: next(loads),
                           sleep_fn=self.slept.append,
                           notify_fn=lambda s, m: self.notices.append((s, m))
                           or {"sent": True}, log=lambda m: None)
        costs = costs or {}

        def run_cell(arm, detail, repeat, budget, export):
            cid = rnd.cell_id(arm, detail, repeat)
            self.calls.append((cid, budget))
            if raise_on == cid:
                raise sb.SandboxError("planted failure")
            cost = costs.get(cid, 1.0)
            self.r.ledger.reserve(cid, budget)
            self.r.ledger.settle(cid, cost)
            return {"cell": cid, "arm": arm, "probe": {"ok": True},
                    "cost_usd": cost, "wall_s": 1.0,
                    "credentials": {"ok": True}}
        self.r.run_cell = run_cell
        self.r.export = lambda: tmp / "export"
        self.r.scorecard = lambda: {"ran": False}


def test_round_runs_repeat_major_and_notifies_once(tmp_path):
    f = Fake(tmp_path)
    out = f.r.run()
    order = [c for c, _ in f.calls]
    assert order == [rnd.cell_id(a, d, r) for r in (1, 2, 3)
                     for d in ("terse", "typical", "full")
                     for a in ("bare", "skill")]
    assert order[:2] == ["bare-terse-r1", "skill-terse-r1"]
    assert out["status"] == "pass" and len(f.notices) == 1
    assert f.notices[0][0] == "finished"


def test_round_skips_cells_that_have_a_result(tmp_path):
    f = Fake(tmp_path)
    f.r.results_dir.mkdir(parents=True)
    for cid in ("bare-terse-r1", "skill-terse-r1"):
        (f.r.results_dir / f"{cid}.json").write_text(json.dumps(
            {"cell": cid, "arm": cid.split("-")[0], "cost_usd": 1.0}))
    f.r.run()
    order = [c for c, _ in f.calls]
    assert len(order) == 16 and order[0] == "bare-typical-r1"


def test_round_stops_at_the_total_budget(tmp_path):
    f = Fake(tmp_path, costs={rnd.cell_id(a, d, r): 60.0 for a in rnd.ARMS
                              for d in rnd.DETAILS for r in rnd.REPEATS})
    out = f.r.run()
    # 7 runs at $60 = $420; an 8th would need 420 + 70 <= 500: yes; a 9th no.
    assert len(f.calls) == 8
    assert f.r.ledger.spent() == 480.0
    assert out["status"] == "violations" and "budget" in out["state"]
    assert all(b == 70.0 for _, b in f.calls)
    assert len(f.notices) == 1


def test_round_budget_counts_a_dead_runs_reservation(tmp_path):
    f = Fake(tmp_path)
    for i in range(7):
        f.r.ledger.reserve(f"dead{i}", 70.0)  # runs that never settled
    out = f.r.run()
    assert f.calls == [] and "budget" in out["state"]


def test_round_stops_on_a_per_run_overrun(tmp_path):
    f = Fake(tmp_path, costs={"skill-terse-r1": 70.5})
    out = f.r.run()
    assert [c for c, _ in f.calls] == ["bare-terse-r1", "skill-terse-r1"]
    assert "overrun" in out["state"] and len(f.notices) == 1


@pytest.mark.parametrize("status", [(0, "usage limit until 12:00Z"),
                                    (3, "weird")])
def test_round_stops_on_a_usage_limit_without_waiting(tmp_path, status):
    f = Fake(tmp_path, status=status)
    out = f.r.run()
    assert f.calls == [] and f.slept == []
    assert "usage limit" in out["state"] and len(f.notices) == 1


def test_round_waits_out_the_load(tmp_path):
    f = Fake(tmp_path, loads=[20.0, 13.0, 3.0] + [1.0] * 50)
    f.r.run(only="bare/terse/1")
    assert f.slept == [rnd.LOAD_SLEEP_S] * 2 and len(f.calls) == 1


def test_round_stops_when_a_run_raises_and_still_notifies_once(tmp_path):
    f = Fake(tmp_path, raise_on="skill-terse-r1")
    out = f.r.run()
    assert len(f.calls) == 2
    assert "skill-terse-r1 could not run" in out["state"]
    assert len(f.notices) == 1 and f.notices[0][0] == "stopped"


def test_dry_run_sends_no_notice(tmp_path):
    f = Fake(tmp_path, dry_run=True)

    def run_cell(arm, detail, repeat, budget, export):
        f.calls.append(rnd.cell_id(arm, detail, repeat))
        return {"probe": {"ok": True}, "dry_run": True}
    f.r.run_cell = run_cell
    out = f.r.run(only="skill/terse/1")
    assert out["state"] == "dry-run" and f.notices == []
    assert not (f.r.dir / "ledger.json").exists()


def test_only_rejects_a_bad_cell():
    with pytest.raises(rnd.RoundError):
        rnd.plan_cells("bare/huge/1")
    assert rnd.plan_cells("skill/terse/1") == [("skill", "terse", 1)]


# ------------------------------------------------------------- sandbox
def test_argv_masks_every_claude_entry_but_the_credentials(tmp_path):
    lay = _layout(tmp_path, "bare")
    argv = sb.build_argv(lay, ["true"])
    m = _mounts(argv)
    sbc = f"{sb.SB_HOME}/.claude"
    assert m[sbc] == ("--bind", str(lay.real_claude_dir))
    for name in os.listdir(lay.real_claude_dir):
        tgt = f"{sbc}/{name}"
        if name == sb.CREDENTIALS:
            assert tgt not in m
            continue
        op, src = m[tgt]
        if name == "projects":
            assert (op, src) == ("--bind", str(lay.projects))
        elif (lay.real_claude_dir / name).is_dir():
            assert op == "--tmpfs", name
        else:
            assert op == "--ro-bind" and Path(src).parent == lay.blanks, name
            assert Path(src).read_text().strip() in ("", "{}")
    # its own namespaces, network included; nothing shares the host's
    assert "--unshare-all" in argv
    assert not any(a.startswith("--share") for a in argv)
    assert "--clearenv" in argv


def test_projects_is_a_per_run_host_dir(tmp_path):
    a = _layout(tmp_path, "bare", run="a")
    b = _layout(tmp_path, "bare", run="b")
    pa = _mounts(sb.build_argv(a, ["true"]))[f"{sb.SB_HOME}/.claude/projects"]
    pb = _mounts(sb.build_argv(b, ["true"]))[f"{sb.SB_HOME}/.claude/projects"]
    assert pa == ("--bind", str(a.run_dir / "claude-projects"))
    assert pb == ("--bind", str(b.run_dir / "claude-projects"))
    assert pa != pb
    # the run's home (where .claude.json lives) is a host dir too
    assert _mounts(sb.build_argv(a, ["true"]))[sb.SB_HOME] == \
        ("--bind", str(a.home))


def test_credentials_are_never_copied(tmp_path):
    lay = _layout(tmp_path, "bare")
    sb.build_argv(lay, ["true"])
    for root, _d, files in os.walk(tmp_path / "round"):
        for name in files:
            p = Path(root) / name
            assert name != sb.CREDENTIALS
            assert "FIXTURE-SECRET" not in p.read_text(errors="replace"), p
    cj = json.loads((lay.home / ".claude.json").read_text())
    assert set(cj["projects"]) == {"/work"} and "mcpServers" not in cj
    assert "emailAddress" not in cj["oauthAccount"]


def test_claude_dir_symlink_or_missing_projects_is_refused(tmp_path):
    lay = _layout(tmp_path, "bare")
    (lay.real_claude_dir / "link").symlink_to("/etc")
    with pytest.raises(sb.SandboxError, match="symlink"):
        sb.build_argv(lay, ["true"])
    (lay.real_claude_dir / "link").unlink()
    shutil.rmtree(lay.real_claude_dir / "projects")
    with pytest.raises(sb.SandboxError, match="projects"):
        sb.build_argv(lay, ["true"])


def test_skill_arm_binds_the_export_read_only(tmp_path):
    exp = tmp_path / "export"
    exp.mkdir()
    lay = _layout(tmp_path, "skill", export=exp)
    m = _mounts(sb.build_argv(lay, ["true"]))
    sk = f"{sb.SB_HOME}/.claude/skills"
    assert m[sk] == ("--tmpfs", None)
    assert m[f"{sk}/chip-flow"] == ("--ro-bind", str(exp))
    assert m[f"{sk}/vde"] == ("--symlink", "chip-flow/skills/vde")
    bare = _mounts(sb.build_argv(_layout(tmp_path, "bare", run="b"), ["true"]))
    assert f"{sk}/chip-flow" not in bare


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t",
                        "GIT_AUTHOR_EMAIL": "t@t.invalid",
                        "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t.invalid"})


def test_export_takes_the_working_tree_minus_corpus_evals_tests(tmp_path):
    repo = tmp_path / "repo"
    for rel in ("corpus/vde/c/holdout/test_x.py", "evals/ladder.py",
                "tests/test_a.py", "docs/design.md", "docs/log.md",
                ".github/ci.yml", "skills/vde/SKILL.md", "engine/a.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(f"{rel}\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "x")
    (repo / "skills/vde/SKILL.md").write_text("edited, not committed\n")
    (repo / "skills/untracked.md").write_text("untracked\n")
    info = sb.export_repo(repo, tmp_path / "exp")
    got = sorted(str(p.relative_to(tmp_path / "exp"))
                 for p in (tmp_path / "exp").rglob("*") if p.is_file())
    assert got == ["docs/design.md", "engine/a.py", "skills/vde/SKILL.md"]
    assert (tmp_path / "exp/skills/vde/SKILL.md").read_text() == \
        "edited, not committed\n"
    assert info["dirty"] is True and len(info["commit"]) == 40
    assert not os.access(tmp_path / "exp/engine/a.py", os.W_OK)


def test_export_of_this_repo_holds_no_corpus_evals_or_tests():
    files = sb.export_files(REPO)
    tops = {f.split("/", 1)[0] for f in files}
    assert not tops & {"corpus", "evals", "tests", ".github"}
    assert [f for f in files if f.startswith("docs/")] == ["docs/design.md"]
    assert "skills/vde/SKILL.md" in files


# --------------------------------------------- stand-in approval, run_cell
@pytest.fixture
def cell_env(tmp_path, monkeypatch):
    home = tmp_path / "realhome"
    _claude_dir(home)
    _claude_json(home)
    monkeypatch.setattr(sb, "real_home", lambda: home)
    r = rnd.Round(tmp_path / "round", status_fn=lambda: (1, "clear"),
                  load_fn=lambda: 0.0, notify_fn=lambda s, m: {},
                  log=lambda m: None)
    (tmp_path / "edatree").mkdir()
    (tmp_path / "claude-elf").write_text("")
    r.layout = lambda arm, rd, export: sb.Layout(
        arm=arm, run_dir=rd, claude_bin=tmp_path / "claude-elf",
        eda_tree=tmp_path / "edatree", eda_bin_dir=tmp_path,
        real_claude_dir=home / ".claude", export_dir=export)
    r.run_probe = lambda layout: {"ok": True, "status": "pass"}
    r.score = lambda *a: {"scored": True}
    r.export_info = {"commit": "c" * 40, "dirty": False}
    return r


def _state(work: Path, status: str, challenge: str = "H1-a1b2c3") -> None:
    p = work / "blocks" / "counter8" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"human": {"H1": {"status": status,
                                              "challenge": challenge}}}))


def test_stand_in_approval_quotes_the_presented_challenge(cell_env):
    cmds = []

    def invoke(layout, cmd, n):
        cmds.append(cmd)
        _state(layout.work, "presented" if n == 0 else "approved")
        return {"n": n, "session_id": "sess-1", "total_cost_usd": 2.0}
    cell_env.invoke = invoke
    res = cell_env.run_cell("skill", "terse", 1, 70.0, Path("/x"))
    assert len(cmds) == 2
    resume = cmds[1]
    assert resume[resume.index("--resume") + 1] == "sess-1"
    assert "H1-a1b2c3" in resume[resume.index("-p") + 1]
    assert resume[resume.index("--max-budget-usd") + 1] == "68.00"
    assert [a["challenge"] for a in res["stand_in_approvals"]] == ["H1-a1b2c3"]
    assert res["cost_usd"] == 4.0
    assert res["repo"]["commit"] and "dirty" in res["repo"]
    assert res["export"]["commit"] == "c" * 40


@pytest.mark.parametrize("arm,status", [("skill", "approved"),
                                        ("bare", "presented")])
def test_no_stand_in_without_a_presented_checkpoint_or_in_bare(
        cell_env, arm, status):
    cmds = []

    def invoke(layout, cmd, n):
        cmds.append(cmd)
        _state(layout.work, status)
        return {"n": n, "session_id": "s", "total_cost_usd": 1.0}
    cell_env.invoke = invoke
    res = cell_env.run_cell(arm, "terse", 1, 70.0,
                            Path("/x") if arm == "skill" else None)
    assert len(cmds) == 1 and res["stand_in_approvals"] == []
    assert "--resume" not in cmds[0]


def test_stand_in_resumes_at_most_max_resumes(cell_env):
    cmds = []

    def invoke(layout, cmd, n):
        cmds.append(cmd)
        _state(layout.work, "presented", f"H1-{n:06x}")
        return {"n": n, "session_id": "s", "total_cost_usd": 1.0}
    cell_env.invoke = invoke
    res = cell_env.run_cell("skill", "full", 2, 70.0, Path("/x"))
    assert len(cmds) == 1 + rnd.MAX_RESUMES
    assert len(res["stand_in_approvals"]) == rnd.MAX_RESUMES


def test_unknown_cost_books_the_whole_budget(cell_env):
    cell_env.invoke = lambda layout, cmd, n: {"n": n}
    res = cell_env.run_cell("bare", "terse", 1, 70.0, None)
    assert res["cost_usd"] == 70.0 and res["cost_known"] is False


# ------------------------------------------------- real sandbox probe
@pytest.mark.slow
@pytest.mark.skipif(shutil.which("bwrap") is None, reason="no bwrap")
def test_probe_in_a_real_sandbox_fails_a_planted_holdout_and_passes_clean(
        tmp_path):
    held = sorted((rnd.RUNG_DIR / "holdout").glob("*.py"))[0]
    keep = []

    def connect(host, port):
        a, b = socket.socketpair()
        keep.append(b)
        return a
    r = rnd.Round(tmp_path / "round", probe_roots=["/work", "/home/eval"],
                  proxy_connect=connect, log=lambda m: None)

    def lay(run):
        lo = _layout(tmp_path, "bare", run=run)
        lo.claude_bin = sb.resolve_claude_bin()
        return lo

    clean = lay("clean")
    v = r.run_probe(clean)
    assert v["ok"], v.get("findings") or v.get("detail")
    assert v["walk"]["files"] >= 2
    denied = {p["target"]: p["refused"] for p in v["net"]["proxy"]}
    assert denied["github.com:443"] is True
    assert denied["api.anthropic.com:443"] is False
    assert all(d["blocked"] for d in v["net"]["direct"])

    planted = lay("planted")
    (planted.work / "notes").mkdir()
    shutil.copy(held, planted.work / "notes" / "copy.py")
    v = r.run_probe(planted)
    assert not v["ok"]
    kinds = {f["kind"] for f in v["findings"]}
    assert "sha256" in kinds and "name" in kinds
    assert not (r.dir / "probe-baseline.json").exists()


# ---------------------------------------------------------------- proxy
def test_proxy_on_a_deep_path_allows_only_the_allowlist(tmp_path):
    import proxy as proxymod
    deep = tmp_path / ("d" * 60) / ("e" * 60)
    keep = []

    def connect(host, port):
        a, b = socket.socketpair()
        keep.append(b)
        return a

    def ask(sock_path, target):
        with socket.socket(socket.AF_UNIX) as c:
            dfd = os.open(sock_path.parent, os.O_PATH | os.O_DIRECTORY)
            try:
                c.connect(f"/proc/self/fd/{dfd}/{sock_path.name}")
            finally:
                os.close(dfd)
            c.sendall(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
            return c.recv(64).split(b"\r\n")[0].decode()
    sock = deep / "proxy.sock"
    log = tmp_path / "proxy.log"
    assert len(str(sock)) > 108
    with proxymod.ConnectProxy(sock, log, connect=connect):
        assert " 403 " in ask(sock, "github.com:443")
        assert " 200 " in ask(sock, "api.anthropic.com:443")
        assert " 403 " in ask(sock, "api.anthropic.com:80")
    s = proxymod.summarize_log(log)
    assert s["denied"] == {"CONNECT github.com:443": 1,
                           "CONNECT api.anthropic.com:80": 1}
    assert s["allowed"] == {"CONNECT api.anthropic.com:443": 1}
