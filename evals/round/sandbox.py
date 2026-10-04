#!/usr/bin/env python
"""sandbox.py - build the bubblewrap sandbox one eval-round run executes in.

    sandbox.py --arm bare|skill --run-dir DIR [--export DIR] -- CMD...
        prints the bwrap argv as JSON (nothing is run)

Every eval run (evals/round/round.py) and its isolation probe (probe.py) run
inside the argv `build_argv` returns. What the sandbox holds, and only that:

  * namespaces: --unshare-all (user, pid, net, ipc, uts, cgroup), so no
    network but a private loopback, no host processes, no host abstract
    sockets; --die-with-parent, --new-session, --clearenv with the explicit
    environment in `sandbox_env`.
  * read-only system: /usr and /etc bound read-only, /bin /sbin /lib /lib64
    recreated as the host has them (symlinks into /usr, or read-only binds).
    No /home, /root, /opt, /srv, /mnt, /media, /run or /var from the host.
    --proc /proc (this pid namespace only), --dev /dev, tmpfs /tmp and
    /var/tmp.
  * the claude binary: the one resolved ELF, bound read-only at its own path,
    and on PATH as /opt/eval/cbin/claude.
  * the EDA tree: bound read-only at its own host path, EDA_TOOLCHAIN set to
    it, and a copy of bin/eda on PATH at /opt/eval/bin/eda.
  * /opt/eval/forward.py and /opt/eval/probe.py, read-only.
  * HOME=/home/eval: a fresh per-run host directory (<run>/home), empty but
    for a minimal .claude.json (`write_claude_json`) and a .gitconfig. It is
    a host directory rather than a tmpfs so `claude --resume` across a run's
    invocations finds its session; it is created new for every run.
  * $HOME/.claude: the REAL ~/.claude directory bound read-write, with EVERY
    entry in it masked except .credentials.json: directories by a tmpfs,
    files by a read-only blank file ("{}" for *.json, else empty; a bind of
    /dev/null reads as EACCES there); `projects` by the run's own
    empty <run>/claude-projects (the transcripts, kept for --resume and the
    record); `skills` by a tmpfs that, in the skill arm only, holds the
    repo export at skills/chip-flow (read-only) and skills/vde -> it.
    The credentials file is NEVER copied: OAuth refresh rotates the refresh
    token, and a copy refreshed inside the sandbox would invalidate the
    host's token and log every session on the box out. Binding the real
    directory lets claude's atomic rename land in the real file. A symlink
    or anything not a file/dir at the top of ~/.claude is refused (it cannot
    be masked safely), and so is a missing `projects` or `skills` (bwrap
    would create the mount point in the real directory).
  * /work: the run's workspace, a host directory OUTSIDE the repo,
    read-write, git-initialised by `prepare_run`.
  * /run/eval-proxy: the run's proxy directory, holding the unix socket of
    the host CONNECT proxy (proxy.py). The sandbox's only way out is
    HTTPS_PROXY=http://127.0.0.1:PROXY_PORT, served by forward.py.

`export_repo` builds the skill arm's read-only copy of the repo from the
WORKING TREE's tracked files (`git ls-files`; uncommitted edits to tracked
files are in it, untracked files are not), leaving out EXPORT_EXCLUDE
(corpus/, evals/, tests/, docs/, .github/) except EXPORT_INCLUDE
(docs/design.md, which the skill's prompts cite). It returns the commit,
whether the tracked tree was dirty, and a sha256 over the exported tree;
round.py puts those in every skill-arm run record.

Each run gets its own run dir, so its home/ and claude-projects/ (where
claude keeps the session transcript `--resume` needs) persist across that
run's invocations and are bound into no other run's sandbox.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

SB_HOME = "/home/eval"
SB_WORK = "/work"
SB_OPT = "/opt/eval"
SB_PROXY_DIR = "/run/eval-proxy"
PROXY_SOCK_NAME = "proxy.sock"
PROXY_PORT = 18080

# Top-level repo paths left out of the skill arm's export: the corpus (the
# held-out tests, formal properties and reference RTL), the evals that
# score against it, the repo's own tests (they hold corpus copies and
# fixtures), the design docs (run logs, examples, the eval design) and CI.
EXPORT_EXCLUDE = ("corpus", "evals", "tests", "docs", ".github")
# ...except these, which the skill's own prompts cite. docs/design.md is the
# plan and contract; it names corpus paths but holds no held-out content
# (the probe fails the run if it ever does).
EXPORT_INCLUDE = ("docs/design.md",)

# The ~/.claude entries given something other than an empty mask.
CREDENTIALS = ".credentials.json"
REQUIRED_ENTRIES = ("projects", "skills")

# Keys of the real ~/.claude.json copied into the sandbox's own: the OAuth
# account claude matches the credentials against, and onboarding done.
# Personal fields of oauthAccount are dropped.
CLAUDE_JSON_KEYS = ("userID", "hasCompletedOnboarding", "lastOnboardingVersion")
OAUTH_ACCOUNT_DROP = ("emailAddress", "displayName", "fullName")


class SandboxError(RuntimeError):
    """The sandbox cannot be built safely. Callers refuse the run."""


@dataclass
class Layout:
    """Host paths of one run, and what goes into its sandbox."""
    arm: str
    run_dir: Path
    claude_bin: Path
    eda_tree: Path
    eda_bin_dir: Path          # host dir holding the `eda` copy
    real_claude_dir: Path
    export_dir: Path | None = None
    extra_env: dict = field(default_factory=dict)

    @property
    def work(self) -> Path:
        return self.run_dir / "work"

    @property
    def home(self) -> Path:
        return self.run_dir / "home"

    @property
    def projects(self) -> Path:
        return self.run_dir / "claude-projects"

    @property
    def proxy_dir(self) -> Path:
        return self.run_dir / "proxy"

    @property
    def blanks(self) -> Path:
        return self.run_dir / "blanks"

    @property
    def proxy_sock(self) -> Path:
        return self.proxy_dir / PROXY_SOCK_NAME


def real_home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def resolve_claude_bin() -> Path:
    """The real claude ELF (~/.local/bin/claude is a symlink to a version)."""
    found = shutil.which("claude") or str(real_home() / ".local/bin/claude")
    p = Path(found).resolve()
    if not p.is_file():
        raise SandboxError(f"no claude binary at {p}")
    return p


def resolve_eda_tree() -> Path:
    t = os.environ.get("EDA_TOOLCHAIN") or str(
        real_home() / ".cc/toolchains/iic-osic-tools-2026.09")
    p = Path(t)
    if not p.is_dir():
        raise SandboxError(f"no EDA tree at {p}; set EDA_TOOLCHAIN")
    return p


def system_args() -> list[str]:
    args = ["--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc"]
    for name in ("bin", "sbin", "lib", "lib32", "lib64", "libx32"):
        p = Path("/") / name
        if p.is_symlink():
            args += ["--symlink", os.readlink(p), str(p)]
        elif p.is_dir():
            args += ["--ro-bind", str(p), str(p)]
    return args


def write_blanks(blanks: Path) -> None:
    """The read-only stand-ins a masked file is bound to: `blank.json`
    ("{}", so a masked settings file still parses) and `blank` (empty).
    /dev/null is not used: a bind of it reads as EACCES in the sandbox."""
    blanks.mkdir(parents=True, exist_ok=True)
    for name, text in (("blank.json", "{}\n"), ("blank", "")):
        p = blanks / name
        if p.exists():
            p.chmod(0o644)
        p.write_text(text, encoding="utf-8")
        p.chmod(0o444)


def claude_dir_masks(real_claude_dir: Path, arm: str, projects_host: Path,
                     export_dir: Path | None, blanks: Path) -> list[str]:
    """Bind the real ~/.claude at $HOME/.claude and mask every entry but the
    credentials file: a directory by a tmpfs, a file by a read-only blank
    (write_blanks). Raises SandboxError on anything it cannot mask."""
    real = Path(real_claude_dir)
    if not real.is_dir() or real.is_symlink():
        raise SandboxError(f"{real} is not a plain directory")
    names = sorted(os.listdir(real))
    for req in REQUIRED_ENTRIES:
        if req not in names:
            raise SandboxError(
                f"{real}/{req} does not exist; bwrap would create its mount "
                "point in the real directory. Create it on the host first.")
    sb = f"{SB_HOME}/.claude"
    args = ["--bind", str(real), sb]
    for name in names:
        if name == CREDENTIALS:
            continue
        p = real / name
        tgt = f"{sb}/{name}"
        st = os.lstat(p)
        if stat.S_ISLNK(st.st_mode):
            raise SandboxError(f"{p} is a symlink; it cannot be masked safely")
        if stat.S_ISDIR(st.st_mode):
            if name == "projects":
                args += ["--bind", str(projects_host), tgt]
            elif name == "skills":
                args += ["--tmpfs", tgt]
                if arm == "skill":
                    if export_dir is None:
                        raise SandboxError("skill arm needs the repo export")
                    args += ["--ro-bind", str(export_dir), f"{tgt}/chip-flow",
                             "--symlink", "chip-flow/skills/vde", f"{tgt}/vde"]
            else:
                args += ["--tmpfs", tgt]
        elif stat.S_ISREG(st.st_mode):
            blank = blanks / ("blank.json" if name.endswith(".json")
                              else "blank")
            args += ["--ro-bind", str(blank), tgt]
        else:
            raise SandboxError(f"{p} is neither a file nor a directory")
    return args


def sandbox_env(layout: Layout) -> dict:
    proxy = f"http://127.0.0.1:{PROXY_PORT}"
    env = {
        "HOME": SB_HOME,
        "USER": "eval",
        "LOGNAME": "eval",
        "SHELL": "/bin/bash",
        "PATH": f"{SB_OPT}/bin:{SB_OPT}/cbin:/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        "TMPDIR": "/tmp",
        "HTTPS_PROXY": proxy,
        "https_proxy": proxy,
        "HTTP_PROXY": proxy,
        "http_proxy": proxy,
        "NO_PROXY": "",
        "no_proxy": "",
        "EDA_TOOLCHAIN": str(layout.eda_tree),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
        "EVAL_SANDBOX": "1",
    }
    if layout.arm == "skill":
        env["CHIP_FLOW_HOME"] = f"{SB_HOME}/.claude/skills/chip-flow"
    env.update(layout.extra_env)
    return env


def build_argv(layout: Layout, command: list[str]) -> list[str]:
    """The full bwrap argv running `command` (cwd /work) behind forward.py."""
    if layout.arm not in ("bare", "skill"):
        raise SandboxError(f"unknown arm {layout.arm!r}")
    if layout.arm == "skill" and layout.export_dir is None:
        raise SandboxError("skill arm needs the repo export")
    for p in (layout.work, layout.home, layout.projects, layout.proxy_dir,
              layout.blanks):
        if not p.is_dir():
            raise SandboxError(f"{p} is missing; prepare_run first")
    argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
            "--hostname", "eval", "--clearenv"]
    for k, v in sorted(sandbox_env(layout).items()):
        argv += ["--setenv", k, v]
    argv += system_args()
    argv += ["--proc", "/proc", "--dev", "/dev",
             "--tmpfs", "/tmp", "--tmpfs", "/var/tmp"]
    cb = str(layout.claude_bin)
    argv += ["--ro-bind", cb, cb, "--symlink", cb, f"{SB_OPT}/cbin/claude"]
    argv += ["--ro-bind", str(layout.eda_tree), str(layout.eda_tree)]
    argv += ["--ro-bind", str(layout.eda_bin_dir), f"{SB_OPT}/bin",
             "--ro-bind", str(HERE / "forward.py"), f"{SB_OPT}/forward.py",
             "--ro-bind", str(HERE / "probe.py"), f"{SB_OPT}/probe.py"]
    argv += ["--bind", str(layout.home), SB_HOME]
    argv += claude_dir_masks(layout.real_claude_dir, layout.arm,
                             layout.projects, layout.export_dir,
                             layout.blanks)
    argv += ["--bind", str(layout.work), SB_WORK,
             "--bind", str(layout.proxy_dir), SB_PROXY_DIR,
             "--chdir", SB_WORK]
    argv += ["--", "/usr/bin/python3", f"{SB_OPT}/forward.py", str(PROXY_PORT),
             f"{SB_PROXY_DIR}/{PROXY_SOCK_NAME}", "--"] + list(command)
    return argv


def write_claude_json(real_json: Path, dest: Path) -> dict:
    """A minimal ~/.claude.json: the OAuth account (minus personal fields),
    the user id and onboarding done, and /work trusted. Nothing else of the
    host's (no projects, MCP servers or history)."""
    cfg: dict = {}
    try:
        src = json.loads(Path(real_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SandboxError(f"cannot read {real_json}: {exc}") from exc
    acct = src.get("oauthAccount")
    if not isinstance(acct, dict):
        raise SandboxError(f"{real_json} has no oauthAccount; log in first")
    cfg["oauthAccount"] = {k: v for k, v in acct.items()
                           if k not in OAUTH_ACCOUNT_DROP}
    for k in CLAUDE_JSON_KEYS:
        if k in src:
            cfg[k] = src[k]
    cfg["hasCompletedOnboarding"] = True
    cfg["autoUpdates"] = False
    cfg["projects"] = {SB_WORK: {"hasTrustDialogAccepted": True,
                                 "hasCompletedProjectOnboarding": True,
                                 "allowedTools": []}}
    dest.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    os.chmod(dest, 0o600)
    return cfg


def _git(args: list[str], cwd: Path, home: Path) -> None:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_AUTHOR_NAME": "eval", "GIT_AUTHOR_EMAIL": "eval@sandbox.invalid",
           "GIT_COMMITTER_NAME": "eval",
           "GIT_COMMITTER_EMAIL": "eval@sandbox.invalid"}
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   capture_output=True)


def prepare_run(run_dir: Path, spec_text: str | None,
                real_claude_json: Path | None = None) -> None:
    """Create a run's host dirs: work/ (git-initialised, spec.md written and
    committed), home/ (.claude.json, .gitconfig), claude-projects/, proxy/,
    blanks/.
    Refuses a run dir whose work/ already exists (runs never reuse one)."""
    run_dir = Path(run_dir)
    work = run_dir / "work"
    if work.exists():
        raise SandboxError(f"{work} already exists; a run never reuses one")
    for d in ("work", "home", "claude-projects", "proxy"):
        (run_dir / d).mkdir(parents=True, exist_ok=d != "work")
    write_blanks(run_dir / "blanks")
    home = run_dir / "home"
    write_claude_json(real_claude_json or real_home() / ".claude.json",
                      home / ".claude.json")
    (home / ".gitconfig").write_text(
        "[user]\n\tname = eval\n\temail = eval@sandbox.invalid\n"
        "[init]\n\tdefaultBranch = main\n[safe]\n\tdirectory = *\n",
        encoding="utf-8")
    _git(["init", "-q", "-b", "main"], work, home)
    if spec_text is not None:
        (work / "spec.md").write_text(spec_text, encoding="utf-8")
        _git(["add", "spec.md"], work, home)
        _git(["commit", "-q", "-m", "spec"], work, home)


def _git_out(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True).stdout


def export_files(repo: Path, exclude: tuple[str, ...] = EXPORT_EXCLUDE,
                 include: tuple[str, ...] = EXPORT_INCLUDE) -> list[str]:
    """The repo-relative paths the export holds: every tracked file (`git
    ls-files`) whose top-level path is not in `exclude`, plus each `include`
    path that is tracked (an exception inside an excluded tree)."""
    tracked = [f for f in _git_out(Path(repo), "ls-files", "-z")
               .decode("utf-8", "surrogateescape").split("\0") if f]
    keep = set(include)
    return [f for f in tracked
            if f.split("/", 1)[0] not in exclude or f in keep]


def export_repo(repo: Path, dest: Path,
                exclude: tuple[str, ...] = EXPORT_EXCLUDE,
                include: tuple[str, ...] = EXPORT_INCLUDE) -> dict:
    """Copy the WORKING TREE's tracked files (`export_files`) to dest and
    make them read-only. Returns the commit, whether the tracked tree was
    dirty (differs from HEAD), a sha256 over every exported path and its
    content, and the counts. A tracked file deleted in the working tree is
    left out (listed). A symlink is copied as a link, and refused if it
    points out of the tree. Refuses if dest exists, or if anything under an
    excluded path but the `include` list survives."""
    repo, dest = Path(repo), Path(dest)
    if dest.exists():
        raise SandboxError(f"{dest} already exists")
    commit = _git_out(repo, "rev-parse", "HEAD").decode().strip()
    dirty = bool(_git_out(repo, "status", "--porcelain",
                          "--untracked-files=no").strip())
    files = export_files(repo, exclude, include)
    dest.mkdir(parents=True)
    h = hashlib.sha256()
    n, missing = 0, []
    for rel in sorted(files):
        src = repo / rel
        out = dest / rel
        if not os.path.lexists(src):
            missing.append(rel)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            link = os.readlink(src)
            tgt = os.path.normpath(os.path.join(os.path.dirname(rel), link))
            if os.path.isabs(link) or tgt == ".." or tgt.startswith("../"):
                raise SandboxError(f"export link leaves the tree: {rel}")
            os.symlink(link, out)
            h.update(f"L {rel} {link}\n".encode())
        elif src.is_file():
            data = src.read_bytes()
            out.write_bytes(data)
            out.chmod((src.stat().st_mode & 0o555) | 0o444)
            h.update(f"F {rel} {hashlib.sha256(data).hexdigest()}\n".encode())
            n += 1
        else:
            missing.append(rel)
    keep = set(include)
    for root, _dirs, names in os.walk(dest):
        for name in names:
            rel = os.path.relpath(os.path.join(root, name), dest)
            if rel.split("/", 1)[0] in exclude and rel not in keep:
                raise SandboxError(f"{rel} is excluded but was exported")
    return {"commit": commit, "dirty": dirty, "files": n,
            "tree_sha256": h.hexdigest(), "missing": missing,
            "excluded": list(exclude), "included": list(include)}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arm", choices=("bare", "skill"), required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--export")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    try:
        eda_tree = resolve_eda_tree()
        layout = Layout(arm=a.arm, run_dir=Path(a.run_dir),
                        claude_bin=resolve_claude_bin(), eda_tree=eda_tree,
                        eda_bin_dir=REPO / "bin",
                        real_claude_dir=real_home() / ".claude",
                        export_dir=Path(a.export) if a.export else None)
        out = {"script": "sandbox.py", "status": "pass",
               "argv": build_argv(layout, cmd or ["true"])}
    except (SandboxError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"script": "sandbox.py", "status": "error",
                          "error": str(exc),
                          "remediation": "fix the path named in the error "
                                         "and build again"}))
        return 2
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
