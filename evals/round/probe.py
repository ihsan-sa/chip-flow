#!/usr/bin/env python3
"""probe.py - the isolation check run inside an eval run's sandbox.

Host side:   probe.py --fingerprints RUNG_DIR      (prints the needle JSON)
In sandbox:  python3 /opt/eval/probe.py < needles.json   (prints the verdict)

round.py runs this inside the exact sandbox argv the run will use (only the
command differs) before EVERY run, and refuses the run unless the verdict is
"pass". A probe that did not run, crashed, or printed no verdict is a
refusal too; that is round.py's rule, not this file's.

The needles are built on the host by `corpus_fingerprints` from the rung's
corpus files at run time (nothing about the corpus is written in this file,
which is itself visible inside the sandbox) and arrive on stdin:
  * sha256 of every file in the rung's rtl/ tb/ holdout/ formal/ faults/ and
    spec.yaml, raw and with whitespace normalised (runs of whitespace
    collapsed to one space, ends stripped);
  * the held-out test function names (holdout/*.py `def test_*`) and the
    formal property and module names (formal/*.sv `LABEL: assert|assume|
    cover`, `module NAME`);
  * the reference RTL's distinctive lines (rtl/*.v lines of 16+ characters
    after whitespace normalisation, not a bare port or keyword line),
    matched whitespace-normalised;
  * the literal string "corpus/vde".
The walk covers every file visible in the sandbox except /proc, /sys, /dev,
the EDA tree (an unpacked tool image; too big to walk per run) and the OAuth
credentials file (a secret, never read). It does not follow symlinks, skips
files over MAX_BYTES (listed), and lists what it could not read. A finding
is any hash, name, distinctive line or string hit, with one exception: the
string "corpus/vde" inside the skill arm's own export (the skill's docs cite
corpus paths that do not exist in the sandbox) is reported as a mention and
does not fail. Hashes, names and RTL lines fail everywhere.

Network: a direct TCP connect to public IPs must fail; a CONNECT through the
sandbox's proxy to each denied target (github.com:443,
raw.githubusercontent.com:443) must be refused; a CONNECT to the API host
must be accepted (the tunnel is closed at once; no request is sent).

$HOME/.claude: lists every entry. Every entry but .credentials.json,
projects and skills must be an empty directory or a blank file; projects
must be empty; settings files must hold no hooks. Bare arm: skills is empty
and no agents, commands, CLAUDE.md or settings with hooks are visible. Skill
arm: skills holds exactly chip-flow and vde, and the vde skill's SKILL.md,
commands/vde.md, the engine's task_router.py and bin/eda resolve.
~/.claude.json must name no project but /work and no MCP server.

Exit 0 pass, 1 findings, 2 error (with a `remediation`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import stat
import sys
import time
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
TEXT_SNIFF = 8192
HELD_DIRS = ("rtl", "tb", "holdout", "formal", "faults")
DENY_TARGETS = ("github.com:443", "raw.githubusercontent.com:443")
ALLOW_TARGET = "api.anthropic.com:443"
PUBLIC_IPS = (("1.1.1.1", 443), ("8.8.8.8", 53), ("140.82.112.3", 443))
SKIP_ALWAYS = ("/proc", "/sys", "/dev")
_WS = re.compile(rb"\s+")
_GENERIC_RTL = re.compile(
    r"^(module\b.*|endmodule|end|begin|else|\)\s*;|(input|output|inout)\b.*"
    r"|always\b.*|wire\b.*|reg\b.*|assign\b.*)$")


def norm(data: bytes) -> bytes:
    return _WS.sub(b" ", data).strip()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- host side
def corpus_fingerprints(rung: Path) -> dict:
    """The needles for one corpus rung, read from its files now."""
    rung = Path(rung)
    files = []
    for d in HELD_DIRS:
        if (rung / d).is_dir():
            files += sorted(p for p in (rung / d).rglob("*") if p.is_file())
    if (rung / "spec.yaml").is_file():
        files.append(rung / "spec.yaml")
    if not files:
        raise ValueError(f"no held-out corpus files under {rung}")
    raw, normed, names, lines = {}, {}, set(), set()
    for p in files:
        data = p.read_bytes()
        rel = str(p.relative_to(rung))
        raw[sha(data)] = rel
        if norm(data):
            normed[sha(norm(data))] = rel
    for p in sorted((rung / "holdout").glob("*.py")):
        names |= set(re.findall(r"^\s*(?:async\s+)?def\s+(test_\w+)",
                                p.read_text(encoding="utf-8"), re.M))
    for p in sorted((rung / "formal").glob("*.sv")):
        text = p.read_text(encoding="utf-8")
        names |= set(re.findall(r"\b([A-Za-z_]\w*)\s*:\s*(?:assert|assume|cover)\b",
                                text))
        names |= set(re.findall(r"^\s*module\s+(\w+)", text, re.M))
    for p in sorted((rung / "rtl").glob("*.v")):
        for line in p.read_text(encoding="utf-8").splitlines():
            n = " ".join(line.split())
            if len(n) >= 16 and not _GENERIC_RTL.match(n) \
                    and not n.startswith("//"):
                lines.add(n)
    if not names:
        raise ValueError(f"no held-out test or property names under {rung}")
    return {"raw_sha256": raw, "norm_sha256": normed,
            "names": sorted(names), "rtl_lines": sorted(lines),
            "strings": ["corpus/vde"]}


# ----------------------------------------------------------- sandbox side
def _classify(data: bytes) -> bool:
    return b"\0" not in data[:TEXT_SNIFF]


def walk(needles: dict, skip_roots: list[str], mention_ok: list[str],
         skip_files: list[str], root: str = "/") -> dict:
    t0 = time.monotonic()
    raw = needles.get("raw_sha256", {})
    normed = needles.get("norm_sha256", {})
    byte_needles = [n.encode() for n in needles.get("names", [])] + \
                   [s.encode() for s in needles.get("strings", [])]
    rx = re.compile(b"|".join(re.escape(n) for n in byte_needles)) \
        if byte_needles else None
    line_rx = re.compile(b"|".join(re.escape(" ".join(l.split()).encode())
                                   for l in needles.get("rtl_lines", []))) \
        if needles.get("rtl_lines") else None
    strings = {s.encode() for s in needles.get("strings", [])}
    skip = [s.rstrip("/") for s in (list(SKIP_ALWAYS) + skip_roots)]
    skip_files = set(skip_files)
    findings, mentions = [], []
    large, unreadable = [], []
    n_files = n_bytes = 0

    def skipped(path: str) -> bool:
        return any(path == s or path.startswith(s + "/") for s in skip)

    stack = [root]
    while stack:
        d = stack.pop()
        try:
            it = list(os.scandir(d))
        except OSError as exc:
            unreadable.append(f"{d}: {exc.strerror}")
            continue
        for e in it:
            path = e.path if d != "/" else "/" + e.name
            if skipped(path):
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError as exc:
                unreadable.append(f"{path}: {exc.strerror}")
                continue
            if stat.S_ISDIR(st.st_mode):
                stack.append(path)
                continue
            if not stat.S_ISREG(st.st_mode) or path in skip_files:
                continue
            if st.st_size > MAX_BYTES:
                large.append(path)
                continue
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                unreadable.append(f"{path}: {exc.strerror}")
                continue
            n_files += 1
            n_bytes += len(data)
            h = sha(data)
            if h in raw:
                findings.append({"kind": "sha256", "path": path,
                                 "matches": raw[h]})
            text = _classify(data)
            if text and data:
                nd = norm(data)
                hn = sha(nd)
                if hn in normed and h not in raw:
                    findings.append({"kind": "sha256_normalised", "path": path,
                                     "matches": normed[hn]})
                if line_rx is not None:
                    m = line_rx.search(nd)
                    if m:
                        findings.append({"kind": "rtl_line", "path": path,
                                         "match": m.group().decode(errors="replace")})
            if rx is not None:
                for m in set(rx.findall(data)):
                    rec = {"kind": "string" if m in strings else "name",
                           "path": path, "match": m.decode(errors="replace")}
                    if m in strings and any(path.startswith(r.rstrip("/") + "/")
                                            for r in mention_ok):
                        mentions.append(rec)
                    else:
                        findings.append(rec)
    return {"findings": findings, "mentions": mentions,
            "files": n_files, "bytes": n_bytes,
            "skipped_large": large, "unreadable": unreadable,
            "skipped_roots": skip, "skipped_files": sorted(skip_files),
            "seconds": round(time.monotonic() - t0, 2)}


def _connect_via_proxy(proxy: str, target: str) -> str:
    host, port = proxy.rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=15) as s:
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        s.settimeout(30)
        buf = b""
        while b"\r\n" not in buf:
            chunk = s.recv(1024)
            if not chunk:
                break
            buf += chunk
    return buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")


def net_checks(proxy: str, deny: list[str], allow: str | None) -> dict:
    out = {"direct": [], "proxy": [], "findings": []}
    for ip, port in PUBLIC_IPS:
        try:
            with socket.create_connection((ip, port), timeout=5):
                pass
            ok, why = False, "connected"
        except OSError as exc:
            ok, why = True, exc.strerror or str(exc)
        out["direct"].append({"target": f"{ip}:{port}", "blocked": ok,
                              "detail": why})
        if not ok:
            out["findings"].append({"kind": "net", "detail":
                                    f"direct TCP to {ip}:{port} connected"})
    try:
        socket.getaddrinfo("github.com", 443)
        out["dns"] = "resolved"
    except OSError as exc:
        out["dns"] = f"fails: {exc}"
    for tgt in deny:
        try:
            status = _connect_via_proxy(proxy, tgt)
        except OSError as exc:
            status = f"error: {exc}"
        refused = " 200 " not in f"{status} "
        out["proxy"].append({"target": tgt, "status": status,
                             "refused": refused})
        if not refused:
            out["findings"].append({"kind": "net", "detail":
                                    f"proxy allowed CONNECT {tgt}"})
    if allow:
        try:
            status = _connect_via_proxy(proxy, allow)
        except OSError as exc:
            status = f"error: {exc}"
        reached = " 200 " in f"{status} "
        out["proxy"].append({"target": allow, "status": status,
                             "refused": not reached})
        if not reached:
            out["findings"].append({"kind": "net", "detail":
                                    f"API host {allow} not reachable through "
                                    f"the proxy: {status}"})
    return out


def _blank(p: Path) -> bool:
    try:
        return p.read_bytes().strip() in (b"", b"{}")
    except OSError:
        return False


def claude_dir_checks(home: Path, arm: str) -> dict:
    cd = home / ".claude"
    findings, listing = [], {}
    if not cd.is_dir():
        return {"listing": {}, "findings": [{"kind": "claude_dir",
                                             "detail": f"{cd} missing"}]}
    for e in sorted(os.listdir(cd)):
        p = cd / e
        if p.is_symlink():
            listing[e] = "symlink"
        elif p.is_dir():
            listing[e] = sorted(os.listdir(p))
        else:
            listing[e] = "file" if e == ".credentials.json" else \
                ("blank" if _blank(p) else f"file {p.stat().st_size} B")
    for e, v in listing.items():
        if e in (".credentials.json", "skills"):
            continue
        if e == "projects":
            if v:
                findings.append({"kind": "claude_dir",
                                 "detail": f"projects is not empty: {v[:5]}"})
            continue
        if v not in ([], "blank"):
            findings.append({"kind": "claude_dir",
                             "detail": f"~/.claude/{e} is visible: "
                                       f"{str(v)[:120]}"})
    for e in listing:
        if e.startswith("settings") and (cd / e).is_file():
            try:
                if b"hooks" in (cd / e).read_bytes():
                    findings.append({"kind": "claude_dir",
                                     "detail": f"~/.claude/{e} has hooks"})
            except OSError:
                pass
    if listing.get(".credentials.json") != "file":
        findings.append({"kind": "claude_dir",
                         "detail": "~/.claude/.credentials.json not visible"})
    skills = listing.get("skills")
    if arm == "bare":
        if skills not in ([], None):
            findings.append({"kind": "claude_dir",
                             "detail": f"bare arm sees skills: {skills}"})
        for e in ("agents", "commands"):
            if listing.get(e) not in ([], None):
                findings.append({"kind": "claude_dir",
                                 "detail": f"bare arm sees {e}"})
        if listing.get("CLAUDE.md") not in ("blank", None):
            findings.append({"kind": "claude_dir",
                             "detail": "bare arm sees CLAUDE.md"})
    else:
        if skills != ["chip-flow", "vde"]:
            findings.append({"kind": "claude_dir",
                             "detail": f"skill arm skills dir is {skills}"})
        need = ["skills/vde/SKILL.md", "skills/vde/commands/vde.md",
                "skills/chip-flow/engine/scripts/task_router.py",
                "skills/chip-flow/bin/eda"]
        for rel in need:
            if not (cd / rel).is_file():
                findings.append({"kind": "skill",
                                 "detail": f"~/.claude/{rel} does not resolve"})
    cj = home / ".claude.json"
    try:
        cfg = json.loads(cj.read_text(encoding="utf-8"))
        extra = [k for k in cfg.get("projects", {}) if k != "/work"]
        if extra or cfg.get("mcpServers"):
            findings.append({"kind": "claude_json",
                             "detail": f"~/.claude.json names projects {extra}"
                                       f" or MCP servers"})
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"kind": "claude_json", "detail": str(exc)})
    return {"listing": listing, "findings": findings}


def run_probe(cfg: dict) -> dict:
    home = Path(cfg.get("home", os.environ.get("HOME", "/home/eval")))
    w = walk(cfg["needles"], cfg.get("skip_roots", []),
             cfg.get("mention_ok", []), cfg.get("skip_files", []),
             cfg.get("root", "/"))
    n = net_checks(cfg.get("proxy", "127.0.0.1:18080"),
                   cfg.get("deny", list(DENY_TARGETS)),
                   cfg.get("allow", ALLOW_TARGET))
    c = claude_dir_checks(home, cfg["arm"])
    findings = w["findings"] + n["findings"] + c["findings"]
    return {"script": "probe.py", "arm": cfg["arm"],
            "status": "pass" if not findings else "violations",
            "findings": findings, "walk": {k: v for k, v in w.items()
                                           if k != "findings"},
            "net": {k: v for k, v in n.items() if k != "findings"},
            "claude_dir": c["listing"],
            "root_entries": sorted(os.listdir(cfg.get("root", "/")))}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fingerprints", metavar="RUNG_DIR")
    a = ap.parse_args(argv)
    try:
        if a.fingerprints:
            out = corpus_fingerprints(Path(a.fingerprints))
            print(json.dumps(out, indent=1))
            return 0
        cfg = json.loads(sys.stdin.read())
        out = run_probe(cfg)
    except Exception as exc:  # noqa: BLE001  (contract: any error -> exit 2)
        print(json.dumps({"script": "probe.py", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}",
                          "remediation": "the probe could not run; the run "
                                         "is refused until it does"}))
        return 2
    print(json.dumps(out, indent=1))
    return 0 if out["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
