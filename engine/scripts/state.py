"""state.py - state.json read/write helpers for the chip-flow engine.

Ported from /hwde's scripts/state.py (docs/design.md 1.3-1.4), schema v3:
`board` becomes `skill` (vde|ade|msde) + `block`, the build-mode concept
(modeslib, PCB-only) is dropped, gate ordering is DERIVED from gates.yaml
instead of a hardcoded pipeline spine (three skills, three different
pipelines - docs/design.md 1.5), and three sections are new: `toolchain`
(the eda image + PDK this run's gates ran against), `jobs` (detached gate
runs, 1.7) and `holdout` (the corpus/testbench holdout hash pinned at
creation, section 2). state.json is still the single source of truth for one
block's run: the orchestrator NEVER carries pipeline state in its context, it
reads the resume summary, acts, and records every transition here. Every
mutating subcommand appends a timestamped event to `history`.

Schema (version 3):
    {
      "version": 3, "skill": "vde|ade|msde", "block": str,
      "workspace": str, "created": ts, "updated": ts,
      "phase": "P0".."P8" | "done",
      "toolchain": {"image", "versions_sha", "pdk", "pdk_sha"} | null,
      "gates": {gate_name: {phase, status: pass|fail, attempts: int,
                            last: {ts, status, failing_count, total,
                                   inputs: {kind: "<norm>:<sha>"|null},
                                   job: id|null},
                            history: [same shape as last, oldest first],
                            stale: [mark]?}},
      "jobs": {id: {gate, pid, started, finished?, status: running|done|dead,
                    log, result?}},
      "holdout": {"sha", "written_by", "ts"} | null,
      "optimise": {"trials", "evaluator_sha", "best": {trial, score}} | null,
      "human": {checkpoint_id: {status: approved|rejected|skipped, ts, note}},
      "artifacts": {name: {path, kind|null, sha256|null, hashed: ts,
                           stale: [mark]?}},
      "open_issues": [{id, gate, phase, fixer, kinds[], severity, count,
                       work_order, status: open|fixing|fixed|escalated|
                       waived, agent, attempts, opened, closed}],
      "next_issue_id": int, "next_job_id": int,
      "budgets": {"fix_loops": {gate_name: remaining}, ...},
      "decisions": [{what, why, phase, ts}],
      "edits": [{ts, class, refs, note, human_hold, gates, gates_marked,
                 stale_artifacts}],
      "spawns": [{ts, role, model, effort?, phase?, tokens?, cost_usd?,
                  note?}],
      "history": [{ts, event, ...detail}]
    }
    mark = {ts, edit_class, refs, human_hold} - stamped by `edit` from
    reference/invalidation.yaml, cleared by record-gate (gates) / re-register
    (artifacts). A gate is FRESH iff its recorded input hashes all match the
    current normalized hashes AND it carries no mark (engine/lib/statelib.py).

CLI (docs/design.md 1.1 script contract: argparse, JSON to stdout, exit 0 ok
/ 2 error; state.py has no violation concept so exit 1 is unused):
    state.py init --workspace DIR --skill vde --block NAME [--phase P0] [--force]
    state.py show|resume|freshness [--workspace DIR | --state FILE]
    state.py set-phase --phase P5 [--force] ...
    state.py record-gate --gate NAME --result gate_result.json [--phase PN] ...
    state.py artifact --name rtl --path rtl ...
    state.py edit --class rtl_edit [--refs U1 U2] [--note TEXT] ...
    state.py rehash [--names rtl tb] ...
    state.py spawn --role fixer --model opus [--effort high] [--tokens N] ...
    state.py toolchain --image PATH [--versions-sha SHA] [--pdk NAME] [--pdk-sha SHA]
    state.py job-start --gate NAME --pid N --log PATH ...
    state.py job-update --job ID --status done|dead [--result FILE] ...
    state.py holdout --written-by tb-writer ...
    state.py decision --what W --why Y ...
    state.py human --checkpoint H1 --status approved [--note N] ...
    state.py issue --id 3 --status fixed [--agent fixer-1] [--bump-attempts] ...
    state.py budget --path fix_loops.lint [--consume] ...
    state.py log --event name [--data JSON] ...
    state.py snapshot --label L [--files F ...] / restore --label L ...

`set-phase` REFUSES to advance past a gate phase whose gate has no recorded
result. `gate.py --workspace <ws>` records the result itself, so the normal
flow never sees the refusal; `--force` is the escape hatch and logs
`phase_forced` with the missing gates.

CLI `log` event names are machine keys: ^[a-z][a-z0-9_-]{0,31}$ - prose
belongs in --data {"msg": ...}. `log --event spawn --data {...}`
additionally appends the data to the first-class `spawns` ledger.

Writes are atomic (unique temp + fsync + os.replace, same directory;
engine/lib/safelib.py). Writer safety: every CLI mutation holds the
OS-exclusive writer lock <state.json>.lock across load->mutate->save, and
State.save() is a base-digest compare-and-swap - a State loaded from bytes X
refuses to overwrite a file that is no longer X (StaleWriteError, exit 2).
`--if-digest SHA` lets a caller pin its own read (show/resume report
`digest`). Snapshot/restore are CONTAINED (no absolute/traversal/symlink
entries, in either direction) and restore is transactional.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

# engine/scripts/state.py -> parent=engine/scripts, parent.parent=engine
ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import safelib  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "state"
VERSION = 3
SKILLS = statelib.SKILLS
# CLI log event names are machine keys: short, greppable, no prose.
EVENT_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
JOB_ID_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
PHASES = ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "done"]
# Human checkpoint ids: free-form "H<n>" markers a skill's phase sequence
# names (docs/design.md 1.4: digital H1/H2, analog H1/H2, msde H2) - never a
# fixed phase->checkpoint table, because that table differs per skill.
CHECKPOINT_RE = re.compile(r"H[1-9][0-9]?\Z")
GATES_YAML = ENGINE / "reference" / "gates.yaml"
DEFAULT_BUDGETS = {
    "fix_loops": {},   # populated per gate on first budget touch, see budget()
}
SNAP_DIR = "state_snapshots"
# Standard workspace layout (docs/design.md 1.4): init owns the scaffold so
# every block starts with the full set regardless of skill (rtl/ for /vde,
# netlist/ for /ade, both harmless if unused).
SUBDIRS = ("brief", "spec", "rtl", "netlist", "tb", "holdout", "formal",
           "synth", "harden", "layout", "sizing", "optimise", "reports",
           "log", SNAP_DIR)
DIGEST_LINE_CAP = 15


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_gates_yaml(path: Path | None = None) -> dict:
    import yaml
    p = path or GATES_YAML
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    gates = data.get("gates") or {}
    if not gates:
        raise CheckError(f"{p}: no gates defined")
    return gates


def applicable_gate_order(skill: str, gates: dict | None = None) -> list[tuple[str, str]]:
    """The gates a block of this skill owes, in pipeline order: every row
    gates.yaml registers for `skill`, sorted by phase then declaration
    order. Single definition of the owed set: set_phase gates advancement on
    it, and resume_summary reports gates_passed/next_gate against it."""
    gates = gates if gates is not None else load_gates_yaml()
    rows = gates.get(skill) or {}
    order = list(rows)
    return [(rows[g].get("phase", "P9"), g)
            for g in sorted(order, key=lambda g: (str(rows[g].get("phase", "P9")),
                                                   order.index(g)))]


def _atomic_write(path: Path, text: str) -> None:
    safelib.atomic_write_text(path, text)


LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")


def _check_label(label) -> str:
    if not isinstance(label, str) or not LABEL_RE.fullmatch(label) \
            or ".." in label:
        raise safelib.ContainmentError(
            f"snapshot label {label!r} refused: labels are a single path "
            "component [A-Za-z0-9][A-Za-z0-9._-]*")
    return label


def _check_snapshot_entry(rel_norm: str, what: str) -> None:
    """state.json and writer-lock files are never snapshot content: a
    restore would roll the state file back under its own writer and replace
    a lock file other writers hold open."""
    name = rel_norm.rsplit("/", 1)[-1]
    if name == "state.json" or name.endswith(".lock"):
        raise safelib.ContainmentError(
            f"{what} {rel_norm!r} refused: state.json and *.lock files are "
            "never snapshotted or restored")


def _snapshot_dir(ws: Path, label: str) -> Path:
    """ws/state_snapshots/<label>, proven inside the workspace: a symlinked
    state_snapshots/ or <label> dir is refused, not followed."""
    return safelib.contained_rel(ws, f"{SNAP_DIR}/{label}",
                                 what="snapshot dir")


class State:
    """In-memory view of one state.json. Mutators record history themselves;
    call save() (atomic) after a batch of mutations."""

    def __init__(self, path: Path, data: dict,
                 base_digest: str | None = None):
        self.path = Path(path)
        self.data = data
        # sha256 of the exact bytes this view was loaded from (None for a
        # fresh init); save() refuses when the file no longer matches (CAS)
        self.base_digest = base_digest

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def init(cls, workspace: Path, skill: str, block: str, phase: str = "P0",
             force: bool = False) -> "State":
        workspace = Path(workspace)
        path = workspace / "state.json"
        if path.exists() and not force:
            raise CheckError(f"{path} already exists (use --force to recreate)")
        if skill not in SKILLS:
            raise CheckError(f"unknown skill {skill!r} (known: {', '.join(SKILLS)})")
        if phase not in PHASES:
            raise CheckError(f"unknown phase {phase!r}")
        workspace.mkdir(parents=True, exist_ok=True)
        for d in SUBDIRS:  # idempotent scaffold; pre-existing content survives
            (workspace / d).mkdir(exist_ok=True)
        ts = now()
        data = {
            "version": VERSION, "skill": skill, "block": block,
            "workspace": str(workspace).replace("\\", "/"),
            "created": ts, "updated": ts, "phase": phase,
            "toolchain": None,
            "gates": {}, "jobs": {}, "holdout": None, "optimise": None,
            "human": {}, "artifacts": {}, "open_issues": [],
            "next_issue_id": 1, "next_job_id": 1,
            "budgets": json.loads(json.dumps(DEFAULT_BUDGETS)),
            "decisions": [], "edits": [], "spawns": [],
            "history": [{"ts": ts, "event": "init", "skill": skill,
                         "block": block, "phase": phase}],
        }
        st = cls(path, data)
        st.save()
        return st

    @classmethod
    def load(cls, path: Path) -> "State":
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CheckError(f"cannot read state file {path}: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckError(f"state file {path} is not valid JSON: {exc}") \
                from exc
        if not isinstance(data, dict):
            raise CheckError(f"state file {path} is not a JSON object")
        if data.get("version") != VERSION:
            raise CheckError(
                f"{path}: state version {data.get('version')!r} unsupported "
                f"(this build reads v{VERSION})")
        return cls(path, data, base_digest=safelib.sha256_bytes(raw))

    def current_digest(self) -> str | None:
        try:
            return safelib.sha256_file(self.path)
        except OSError:
            return None

    def save(self) -> None:
        """Atomic + compare-and-swap: under the writer lock, refuse when the
        on-disk bytes no longer match the bytes this view was loaded from
        (another writer landed in between). Never merges, never retries -
        the caller reloads and redoes its mutation on the fresh state."""
        with safelib.writer_lock(self.path, what="state.json"):
            if self.base_digest is not None:
                cur = self.current_digest()
                if cur != self.base_digest:
                    raise safelib.StaleWriteError(
                        f"{self.path} changed since it was loaded (base "
                        f"{self.base_digest[:12]}, now "
                        f"{(cur or 'missing')[:12]}) - another writer landed; "
                        "reload and redo the mutation, nothing was written")
            self.data["updated"] = now()
            text = json.dumps(self.data, indent=1)
            _atomic_write(self.path, text)
            self.base_digest = safelib.sha256_bytes(text.encode("utf-8"))

    def _log(self, event: str, **detail) -> None:
        self.data["history"].append({"ts": now(), "event": event, **detail})

    def _skill(self) -> str:
        return self.data.get("skill") or ""

    # ---- mutators --------------------------------------------------------
    def gate_coverage(self, phase: str) -> tuple[list[str], list[str]]:
        """(gates owed BEFORE `phase` with no recorded result, gates whose
        last recorded result is a fail). A gate at phase P is owed once the
        run moves past P."""
        idx = PHASES.index(phase)
        owed = [(ph, g) for ph, g in applicable_gate_order(self._skill())
                if ph in PHASES and PHASES.index(ph) < idx]
        gates = self.data["gates"]
        missing = [f"{g} ({ph})" for ph, g in owed
                   if not (gates.get(g) or {}).get("status")]
        failed = [g for _, g in owed
                  if (gates.get(g) or {}).get("status") == "fail"]
        return missing, failed

    def set_phase(self, phase: str, require_gates: bool = True) -> list[dict]:
        """Record the phase; return warn-only digest-discipline findings for
        the phase just left (drift becomes recorded fact, exit 0).

        Advancing past a gate phase whose gate has no recorded result is
        REFUSED (CheckError): you cannot leave a gate phase without evidence
        in state.json. `require_gates=False` (CLI --force) is the deliberate
        escape hatch and records itself in history.
        """
        if phase not in PHASES:
            raise CheckError(f"unknown phase {phase!r}")
        prev = self.data["phase"]
        warnings: list[dict] = []
        advancing = (prev in PHASES
                     and PHASES.index(phase) > PHASES.index(prev))
        if advancing:
            missing, failed = self.gate_coverage(phase)
            if missing and require_gates:
                raise CheckError(
                    f"cannot advance {prev} -> {phase}: no recorded gate "
                    f"result for {', '.join(missing)}. Run the gate with "
                    "gate.py --workspace <ws> (it records the result itself) "
                    "or state.py record-gate --gate <g> --result <file>; "
                    "--force advances anyway and says so in history")
            if missing:
                warnings.append({
                    "kind": "gate_coverage",
                    "msg": f"advanced to {phase} with no recorded result for "
                           f"{', '.join(missing)} (--force)"})
                self._log("phase_forced", phase=phase, prev=prev,
                          missing=missing)
            if failed:
                warnings.append({
                    "kind": "gate_coverage",
                    "msg": f"advanced to {phase} with {', '.join(failed)} "
                           "recorded FAIL - the fix loop re-records a pass "
                           "before the run moves on"})
        self.data["phase"] = phase
        self._log("phase", phase=phase, prev=prev)
        if prev != phase and re.fullmatch(r"P\d+", prev or ""):
            digest = self.path.parent / "log" / f"{prev}-digest.md"
            if not digest.is_file():
                warnings.append({
                    "kind": "digest_discipline",
                    "msg": f"log/{prev}-digest.md missing for the phase just "
                           "left"})
            else:
                n = len(digest.read_text(encoding="utf-8",
                                         errors="replace").splitlines())
                if n > DIGEST_LINE_CAP:
                    warnings.append({
                        "kind": "digest_discipline",
                        "msg": f"log/{prev}-digest.md is {n} lines "
                               f"(cap {DIGEST_LINE_CAP})"})
        return warnings

    def record_gate(self, gate: str, result: dict, phase: str | None = None) -> dict:
        status = result.get("status")
        if status not in ("pass", "fail"):
            raise CheckError(f"gate result status must be pass|fail, "
                             f"got {status!r}")
        # when the result carries the report's stamped input digest, it must
        # match the CURRENT primary input - otherwise the result describes a
        # different or stale artifact and recording it would mark the gate
        # fresh-pass falsely.
        digest = result.get("input_digest")
        if digest:
            imap = self._imap()
            kinds = (imap["gate_inputs"].get(self._skill()) or {}).get(gate) or []
            if kinds:
                rel, cur = statelib.hash_kind(
                    self.path.parent, kinds[0], imap, self.data["artifacts"])
                if cur != digest:
                    raise CheckError(
                        f"gate result input_digest does not match the "
                        f"current {kinds[0]} ({rel}) - the result describes "
                        "a different or stale artifact; re-run the gate "
                        "against the current file")
        entry = {"ts": now(), "status": status,
                 "failing_count": result.get("failing_count", 0),
                 "total": (result.get("counts") or {}).get("total", 0),
                 "inputs": self._hash_gate_inputs(gate),
                 "job": result.get("job")}
        g = self.data["gates"].setdefault(
            gate, {"phase": phase or result.get("phase"), "status": None,
                   "attempts": 0, "last": None, "history": []})
        if phase:
            g["phase"] = phase
        g["attempts"] += 1
        g["status"] = status
        g["last"] = entry
        g["history"].append(entry)
        # the gate just ran against the CURRENT inputs: whatever edit marks
        # it carried are resolved (pass or fail - the result is current
        # either way)
        g.pop("stale", None)
        self._log("gate", gate=gate, status=status, attempt=g["attempts"],
                  failing_count=entry["failing_count"])
        return g

    def _imap(self) -> dict:
        return statelib.load_map()

    def _hash_gate_inputs(self, gate: str) -> dict:
        """Normalized hashes of every artifact the gate reads (statelib),
        resolved against the state file's own directory - the freshness key
        the result stays valid under. Hashed kinds are silently
        auto-registered in the artifacts registry."""
        imap = self._imap()
        ws = self.path.parent
        registry = self.data["artifacts"]
        inputs: dict[str, str | None] = {}
        for kind in (imap["gate_inputs"].get(self._skill()) or {}).get(gate, []):
            rel, sha = statelib.hash_kind(ws, kind, imap, registry)
            inputs[kind] = sha
            entry = registry.get(kind)
            if not isinstance(entry, dict):
                entry = {}
            entry.update({"path": rel, "kind": kind, "sha256": sha,
                          "hashed": now()})
            registry[kind] = entry          # stale marks (if any) survive
        return inputs

    def set_artifact(self, name: str, path: str) -> None:
        """Register/refresh an artifact by name. Explicit registration means
        "this file was (re)produced": the entry is re-hashed and any stale
        marks on it are cleared."""
        rel = str(path).replace("\\", "/")
        imap = self._imap()
        kind = name if name in imap["artifact_kinds"] else None
        norm = (imap["artifact_kinds"][kind]["norm"] if kind
                else statelib.norm_for_path(self.path.parent / rel))
        sha = statelib.hash_artifact(self.path.parent / rel, norm)
        self.data["artifacts"][name] = {"path": rel, "kind": kind,
                                        "sha256": sha, "hashed": now()}
        self._log("artifact", name=name, path=rel)

    def apply_edit(self, edit_class: str, refs: list[str] | None = None,
                   note: str | None = None) -> dict:
        """Record a declared edit: stamp the invalidation map's stale set.
        Marks land on RECORDED gates (an unrun gate has no result to
        distrust) and on the mapped derived artifacts (registry entries are
        created at their current hash when absent, so later regeneration is
        detectable). Returns the full mapped sets + the ceremony weight."""
        imap = self._imap()
        skill_classes = imap["edit_classes"].get(self._skill()) or {}
        ec = skill_classes.get(edit_class)
        if ec is None:
            raise CheckError(
                f"unknown edit class {edit_class!r} for skill "
                f"{self._skill()!r} (known: {', '.join(sorted(skill_classes))})")
        ts = now()
        mark = {"ts": ts, "edit_class": edit_class, "refs": refs or [],
                "human_hold": ec["human_hold"]}
        marked_gates = []
        for gname in ec["gates"]:
            g = self.data["gates"].get(gname)
            if g is not None:
                g.setdefault("stale", []).append(mark)
                marked_gates.append(gname)
        ws = self.path.parent
        registry = self.data["artifacts"]
        for kind in ec["stale_artifacts"]:
            entry = registry.get(kind)
            if not isinstance(entry, dict):
                rel, sha = statelib.hash_kind(ws, kind, imap, registry)
                entry = {"path": rel, "kind": kind, "sha256": sha,
                         "hashed": ts}
                registry[kind] = entry
            entry.setdefault("stale", []).append(mark)
        rec = {"ts": ts, "class": edit_class, "refs": refs or [],
               "note": note, "human_hold": ec["human_hold"],
               "gates": list(ec["gates"]), "gates_marked": marked_gates,
               "stale_artifacts": list(ec["stale_artifacts"])}
        self.data["edits"].append(rec)
        self._log("edit", edit_class=edit_class, refs=refs or [],
                  human_hold=ec["human_hold"])
        return rec

    def rehash(self, names: list[str] | None = None) -> dict:
        """Re-hash artifacts. Explicit --names = "I regenerated these": their
        stale marks are force-cleared. A bare rehash refreshes every
        registered entry plus every standard kind whose file exists, clearing
        marks only where the hash actually CHANGED."""
        imap = self._imap()
        ws = self.path.parent
        registry = self.data["artifacts"]
        explicit = names is not None
        if names is None:
            names = sorted(set(registry) | {
                k for k in imap["artifact_kinds"]
                if (ws / statelib.kind_path(k, imap, registry)).exists()})
        out = {}
        for name in names:
            entry = registry.get(name)
            if not isinstance(entry, dict):
                if name not in imap["artifact_kinds"]:
                    raise CheckError(f"unknown artifact {name!r} (not "
                                     "registered, not a standard kind)")
                entry = {"kind": name}
            kind = entry.get("kind")
            if kind not in imap["artifact_kinds"]:
                kind = None
            rel = entry.get("path") or statelib.kind_path(kind or name, imap,
                                                           registry)
            norm = (imap["artifact_kinds"][kind]["norm"] if kind
                    else statelib.norm_for_path(ws / rel))
            old = entry.get("sha256")
            sha = statelib.hash_artifact(ws / rel, norm)
            entry.update({"path": rel, "kind": kind, "sha256": sha,
                          "hashed": now()})
            if entry.get("stale") and (explicit or sha != old):
                entry.pop("stale", None)
            registry[name] = entry
            out[name] = {"path": rel, "sha256": sha, "changed": sha != old,
                         "stale_marks": len(entry.get("stale") or [])}
        self._log("rehash", names=sorted(out))
        return out

    def record_spawn(self, record: dict) -> dict:
        """Append a subagent spawn to the first-class ledger."""
        rec = {"ts": now(), **{k: v for k, v in record.items()
                               if v is not None}}
        self.data["spawns"].append(rec)
        self._log("spawn", role=rec.get("role"), model=rec.get("model"))
        return rec

    def set_toolchain(self, image: str, versions_sha: str | None = None,
                      pdk: str | None = None, pdk_sha: str | None = None) -> dict:
        rec = {"image": image, "versions_sha": versions_sha, "pdk": pdk,
               "pdk_sha": pdk_sha, "ts": now()}
        self.data["toolchain"] = rec
        self._log("toolchain", image=image, pdk=pdk)
        return rec

    def record_holdout(self, written_by: str) -> dict:
        """Hash holdout/ into state.holdout at creation (section 2): a
        held-out test's own hash, pinned once, so a run can tell whether
        the tb-writer's holdout set has changed under it."""
        imap = self._imap()
        rel, sha = statelib.hash_kind(self.path.parent, "holdout", imap,
                                      self.data["artifacts"])
        rec = {"sha": sha, "written_by": written_by, "ts": now()}
        self.data["holdout"] = rec
        self._log("holdout", written_by=written_by, sha=sha)
        return rec

    # ---- jobs (docs/design.md 1.7) ---------------------------------------
    def start_job(self, gate: str, pid: int, log: str) -> tuple[str, dict]:
        jid = str(self.data.get("next_job_id", 1))
        self.data["next_job_id"] = int(jid) + 1
        rec = {"gate": gate, "pid": pid, "started": now(), "finished": None,
               "status": "running", "log": log, "result": None}
        self.data["jobs"][jid] = rec
        self._log("job_start", job=jid, gate=gate, pid=pid)
        return jid, rec

    def update_job(self, job_id: str, status: str | None = None,
                   result: dict | None = None) -> dict:
        job = self.data["jobs"].get(job_id)
        if job is None:
            raise CheckError(f"no job {job_id!r}")
        if status:
            if status not in ("running", "done", "dead"):
                raise CheckError(f"bad job status {status!r}")
            job["status"] = status
            if status in ("done", "dead"):
                job["finished"] = now()
        if result is not None:
            job["result"] = result
        self._log("job_update", job=job_id, status=job["status"])
        return job

    def set_mode(self, *a, **kw):  # pragma: no cover - explicit removal
        raise CheckError(
            "state.py mode was /hwde's build-mode concept (PCB geometry "
            "relaxation) and does not port; chip-flow has no equivalent")

    def add_decision(self, what: str, why: str, phase: str | None = None) -> None:
        self.data["decisions"].append(
            {"what": what, "why": why, "phase": phase or self.data["phase"],
             "ts": now()})
        self._log("decision", what=what)

    def record_human(self, checkpoint: str, status: str,
                     note: str | None = None) -> None:
        if not CHECKPOINT_RE.fullmatch(checkpoint or ""):
            raise CheckError(f"checkpoint must match H<n>, got {checkpoint!r}")
        if status not in ("approved", "rejected", "skipped"):
            raise CheckError("human status must be approved|rejected|skipped")
        self.data["human"][checkpoint] = {"status": status, "ts": now(),
                                          "note": note}
        self._log("human", checkpoint=checkpoint, status=status)

    def open_issue(self, issue: dict) -> dict:
        iid = self.data["next_issue_id"]
        self.data["next_issue_id"] = iid + 1
        rec = {"id": iid, "status": "open", "agent": None, "attempts": 0,
               "opened": now(), "closed": None, **issue}
        self.data["open_issues"].append(rec)
        self._log("issue_open", id=iid, gate=rec.get("gate"),
                  fixer=rec.get("fixer"), kinds=rec.get("kinds"))
        return rec

    def update_issue(self, iid: int, status: str | None = None,
                     agent: str | None = None, bump: bool = False) -> dict:
        for rec in self.data["open_issues"]:
            if rec["id"] == iid:
                if status:
                    if status not in ("open", "fixing", "fixed", "escalated",
                                      "waived"):
                        raise CheckError(f"bad issue status {status!r}")
                    rec["status"] = status
                    if status in ("fixed", "waived"):
                        rec["closed"] = now()
                if agent:
                    rec["agent"] = agent
                if bump:
                    rec["attempts"] += 1
                self._log("issue", id=iid, status=rec["status"],
                          agent=rec["agent"], attempts=rec["attempts"])
                return rec
        raise CheckError(f"no issue with id {iid}")

    def budget(self, dotted: str, consume: bool = False, default: int = 3) -> int:
        node = self.data["budgets"]
        keys = dotted.split(".")
        for i, k in enumerate(keys[:-1]):
            if not isinstance(node, dict):
                raise CheckError(f"unknown budget path {dotted!r}")
            if k not in node:
                node[k] = {}
                self._log("budget_defaulted", path=".".join(keys[:i + 1]))
            node = node[k]
        leaf = keys[-1]
        if leaf not in node:
            node[leaf] = default
            self._log("budget_defaulted", path=dotted, value=default)
        if consume:
            if node[leaf] <= 0:
                raise CheckError(f"budget {dotted} exhausted")
            node[leaf] -= 1
            self._log("budget", path=dotted, remaining=node[leaf])
        return node[leaf]

    # ---- snapshots (fix-loop safety net) ----------------------------------
    def _workspace(self) -> Path:
        return Path(self.data["workspace"])

    def _default_snapshot_rels(self, ws: Path) -> list[str]:
        """Every registered artifact's own file(s) - a FILE artifact as
        itself, a DIRECTORY artifact (rtl/, tb/, formal/, holdout/, ...)
        expanded to every file it actually contains.

        M5, found by actually running the fix loop for real: this used to
        be `is_file()`-only, which is true for exactly ZERO of /vde's own
        design artifacts (rtl/tb/formal/holdout/harden/layout are all
        directory-kind, docs/design.md 1.4/1.6) - a `state.py snapshot`
        with no explicit --files silently protected NOTHING for precisely
        the artifact classes (rtl_edit/tb_edit/formal_edit) the fix loop's
        own rollback step exists to protect. Same __pycache__/.pyc/.pyo
        exclusion as statelib's own dir_text hashing (a cocotb import's
        bytecode cache is not part of the design and must never count as
        something a restore should bring back)."""
        rels: list[str] = []
        for a in self.data["artifacts"].values():
            if not isinstance(a, dict) or not a.get("path"):
                continue
            p = ws / a["path"]
            if p.is_file():
                rels.append(a["path"])
            elif p.is_dir():
                rels.extend(
                    f.relative_to(ws).as_posix()
                    for f in sorted(p.rglob("*"))
                    if f.is_file() and "__pycache__" not in f.parts
                    and f.suffix not in (".pyc", ".pyo"))
        return rels

    def snapshot(self, label: str, files: list[str] | None = None) -> dict:
        """Contained copy of workspace files into state_snapshots/<label>.
        Every entry is proven inside the workspace (no absolute/traversal/
        symlink entries) before a byte is copied."""
        ws = self._workspace()
        label = _check_label(label)
        dest = _snapshot_dir(ws, label)
        rels = files or self._default_snapshot_rels(ws)
        plan = []
        for rel in rels:
            rel_norm = str(rel).replace("\\", "/")
            src = safelib.contained_rel(ws, rel_norm, what="snapshot entry")
            if src.is_symlink() or not src.is_file():
                raise CheckError(
                    f"snapshot source missing or not a regular file: {src}")
            if rel_norm.split("/")[0] == SNAP_DIR:
                raise safelib.ContainmentError(
                    f"snapshot entry {rel_norm!r} lies inside {SNAP_DIR}/")
            _check_snapshot_entry(rel_norm, "snapshot entry")
            plan.append((rel_norm, src))
        if dest.exists():
            shutil.rmtree(dest)
        manifest = []
        for rel_norm, src in plan:
            out = safelib.contained_rel(dest, rel_norm, what="snapshot copy")
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            manifest.append({"path": rel_norm,
                             "sha256": safelib.sha256_file(out)})
        safelib.atomic_write_json(
            dest / "manifest.json",
            {"label": label, "ts": now(), "files": manifest})
        self._log("snapshot", label=label, files=len(manifest))
        return {"label": label, "files": manifest}

    def restore(self, label: str) -> dict:
        """Transactional restore: validate + stage + verify EVERY file
        beside its target, then swap each into place with os.replace. Any
        failure before the swap phase leaves the workspace byte-for-byte
        untouched."""
        ws = self._workspace()
        label = _check_label(label)
        dest = _snapshot_dir(ws, label)
        man = checklib.load_json(dest / "manifest.json",
                                 f"snapshot {label} manifest")
        files = man.get("files") if isinstance(man, dict) else None
        if not isinstance(files, list):
            raise CheckError(f"snapshot {label} manifest has no files list")
        plan = []
        for f in files:
            if not isinstance(f, dict) or not isinstance(f.get("path"), str) \
                    or not isinstance(f.get("sha256"), str):
                raise CheckError(f"snapshot {label} manifest entry malformed: "
                                 f"{f!r}")
            rel = f["path"]
            _check_snapshot_entry(rel.replace("\\", "/"), "snapshot file")
            src = safelib.contained_rel(dest, rel, what="snapshot file")
            if src.is_symlink() or not src.is_file():
                raise CheckError(f"snapshot file missing or not a regular "
                                 f"file: {src}")
            target = safelib.contained_rel(ws, rel, what="restore target")
            if target.exists() and (target.is_symlink()
                                    or not target.is_file()):
                raise safelib.ContainmentError(
                    f"restore target {target} is not a regular file")
            safelib.sweep_stale_stage_temps(target)
            plan.append((rel, src, target, f["sha256"]))
        staged: list[tuple[Path, Path, str, str]] = []
        restored = []
        try:
            for rel, src, target, sha in plan:
                tmp = safelib.stage_copy(src, target)
                staged.append((tmp, target, sha, rel))
                safelib.fault("restore.staged", rel=rel, n=len(staged))
            for tmp, target, sha, rel in staged:
                got = safelib.sha256_file(tmp)
                if got != sha:
                    raise CheckError(
                        f"restore hash mismatch for {rel} (snapshot {label} "
                        f"is corrupt: {got[:12]} != {sha[:12]}) - nothing "
                        "restored")
            safelib.fault("restore.verified", label=label)
            for tmp, target, sha, rel in staged:
                if target.is_symlink():
                    raise safelib.ContainmentError(
                        f"restore target {target} became a symlink")
                os.replace(tmp, target)
                restored.append(rel)
        finally:
            for tmp, _t, _s, _r in staged:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
        self._log("restore", label=label, files=len(restored))
        return {"label": label, "restored": restored}

    # ---- freshness -------------------------------------------------------
    def freshness(self) -> dict:
        """Read-only two-layer freshness report (statelib): per recorded gate
        hash validity + stale marks, per artifact registered-vs-current."""
        return statelib.freshness_report(self.data, self.path.parent,
                                         self._imap())

    # ---- resume ----------------------------------------------------------
    def resume_summary(self) -> dict:
        gates = self.data["gates"]
        order = applicable_gate_order(self._skill())
        passed = [g for _, g in order
                  if gates.get(g, {}).get("status") == "pass"]
        next_gate = None
        for ph, g in order:
            if gates.get(g, {}).get("status") != "pass":
                next_gate = {"phase": ph, "gate": g}
                break
        # "escalated" belongs here too (M5, found running the fix loop for
        # real): it is the fix loop's OWN outcome for a finding that cannot
        # be closed by looping again (docs/design.md, "Escalate: ... a
        # human decides") - the single most important thing for a resumed
        # session to see, not less. Only "fixed"/"waived" are genuinely
        # closed and belong out of this list.
        open_issues = [i for i in self.data["open_issues"]
                       if i["status"] in ("open", "fixing", "escalated")]
        running_jobs = [jid for jid, j in self.data["jobs"].items()
                        if j.get("status") == "running"]
        last = self.data["history"][-1] if self.data["history"] else None
        fresh = self.freshness()
        # phase is workflow position, never a release certificate: resume
        # surfaces the DERIVED disposition beside it. Advisory here: any
        # attest failure degrades to null, never breaks resume.
        disposition_error = None
        try:
            import attest  # noqa: E402  (sibling script, scripts/ on sys.path)
            disposition = attest.disposition(self.path.parent)["disposition"]
        except Exception as exc:  # noqa: BLE001
            disposition = None
            disposition_error = f"{type(exc).__name__}: {exc}"
        return {
            "script": SCRIPT, "skill": self.data["skill"],
            "block": self.data["block"],
            "release_disposition": disposition,
            **({"release_disposition_error": disposition_error}
               if disposition_error else {}),
            "workspace": self.data["workspace"], "phase": self.data["phase"],
            "gates_passed": passed, "next_gate": next_gate,
            "gates_passed_fresh": [g for g in passed
                                   if fresh["gates"][g]["fresh"]],
            "gates_stale": fresh["summary"]["stale"],
            "gates_freshness_unknown": fresh["summary"]["unknown"],
            "human_hold_pending": fresh["summary"]["human_hold_pending"],
            "open_issues": open_issues, "running_jobs": running_jobs,
            "budgets": self.data["budgets"],
            "artifacts": self.data["artifacts"], "last_event": last,
        }


# ---- CLI ----------------------------------------------------------------
def _find_state(args) -> Path:
    if getattr(args, "state", None):
        return Path(args.state)
    ws = getattr(args, "workspace", None)
    if ws:
        return Path(ws) / "state.json"
    p = Path("state.json")
    if p.exists():
        return p
    raise CheckError("give --state FILE or --workspace DIR (no ./state.json)")


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--state", help="path to state.json")
        p.add_argument("--workspace", help="workspace dir (state.json inside)")
        p.add_argument("--out", help="write result JSON here instead of stdout")
        p.add_argument("--if-digest",
                       help="compare-and-swap pin: refuse unless the current "
                            "state.json sha256 equals this (show/resume "
                            "report `digest`)")

    p = sub.add_parser("init", help="create a new state.json")
    p.add_argument("--workspace", required=True)
    p.add_argument("--skill", required=True, choices=SKILLS)
    p.add_argument("--block", required=True)
    p.add_argument("--phase", default="P0")
    p.add_argument("--force", action="store_true")
    p.add_argument("--out")

    for name in ("show", "resume", "freshness"):
        common(sub.add_parser(name))

    p = sub.add_parser("set-phase")
    common(p)
    p.add_argument("--phase", required=True)
    p.add_argument("--force", action="store_true",
                   help="advance even when an owed gate has no recorded "
                        "result (logged as phase_forced with the list)")

    p = sub.add_parser("record-gate")
    common(p)
    p.add_argument("--gate", required=True)
    p.add_argument("--result", required=True,
                   help="gate.py result JSON (has status/failing_count)")
    p.add_argument("--phase")

    p = sub.add_parser("artifact")
    common(p)
    p.add_argument("--name", required=True)
    p.add_argument("--path", required=True)

    p = sub.add_parser("edit", help="record a declared edit; stamps the "
                       "invalidation.yaml stale set")
    common(p)
    p.add_argument("--class", dest="edit_class", required=True,
                   help="edit class from reference/invalidation.yaml")
    p.add_argument("--refs", nargs="*", default=None,
                   help="module/file names the edit touches (for the record)")
    p.add_argument("--note")

    p = sub.add_parser("rehash", help="re-hash artifacts; --names = "
                       "force-clear their stale marks (regenerated)")
    common(p)
    p.add_argument("--names", nargs="*", default=None)

    p = sub.add_parser("spawn", help="record a subagent spawn in the ledger")
    common(p)
    p.add_argument("--role", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort")
    p.add_argument("--phase")
    p.add_argument("--tokens", type=int)
    p.add_argument("--cost-usd", type=float, dest="cost_usd")
    p.add_argument("--note")

    p = sub.add_parser("toolchain", help="record the eda image + PDK this "
                       "run's gates ran against")
    common(p)
    p.add_argument("--image", required=True)
    p.add_argument("--versions-sha")
    p.add_argument("--pdk")
    p.add_argument("--pdk-sha")

    p = sub.add_parser("job-start", help="record a detached gate job "
                       "(jobs.py calls this)")
    common(p)
    p.add_argument("--gate", required=True)
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--log", required=True)

    p = sub.add_parser("job-update")
    common(p)
    p.add_argument("--job", required=True)
    p.add_argument("--status", choices=("running", "done", "dead"))
    p.add_argument("--result", help="job result JSON file")

    p = sub.add_parser("holdout", help="hash holdout/ into state.holdout")
    common(p)
    p.add_argument("--written-by", required=True)

    p = sub.add_parser("decision")
    common(p)
    p.add_argument("--what", required=True)
    p.add_argument("--why", required=True)
    p.add_argument("--phase")

    p = sub.add_parser("human")
    common(p)
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--note")

    p = sub.add_parser("issue")
    common(p)
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--status")
    p.add_argument("--agent")
    p.add_argument("--bump-attempts", action="store_true")

    p = sub.add_parser("budget")
    common(p)
    p.add_argument("--path", required=True, dest="bpath",
                   help="dotted path, e.g. fix_loops.lint")
    p.add_argument("--consume", action="store_true")

    p = sub.add_parser("log")
    common(p)
    p.add_argument("--event", required=True)
    p.add_argument("--data", help="extra JSON object merged into the event")

    p = sub.add_parser("snapshot")
    common(p)
    p.add_argument("--label", required=True)
    p.add_argument("--files", nargs="*",
                   help="workspace-relative files (default: file artifacts)")

    p = sub.add_parser("restore")
    common(p)
    p.add_argument("--label", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "init":
        st = State.init(Path(args.workspace), args.skill, args.block,
                        args.phase, args.force)
        return {"script": SCRIPT, "cmd": "init", "state": str(st.path),
                "skill": args.skill, "block": args.block,
                "phase": args.phase, "subdirs": list(SUBDIRS)}, args.out

    state_path = _find_state(args)
    result: dict = {"script": SCRIPT, "cmd": args.cmd}

    if args.cmd in ("show", "resume", "freshness"):  # read-only: never saves
        st = State.load(state_path)
        _check_pin(st, args)
        if args.cmd == "show":
            return {**result, **st.data, "digest": st.base_digest}, args.out
        if args.cmd == "resume":
            return {**result, **st.resume_summary(),
                    "digest": st.base_digest}, args.out
        return {**result, **st.freshness()}, args.out

    # one OS-exclusive hold across load -> mutate -> save, so two CLI writers
    # on one workspace serialize instead of losing an update - and never on
    # a state file that is not there: writer_lock would mkdir a typo'd
    # --workspace and leave a stray .lock behind
    if not state_path.is_file():
        raise CheckError(f"no state file at {state_path} (wrong --workspace/"
                         "--state? run `state.py init` for a new workspace)")
    with safelib.writer_lock(state_path, what="state.json"):
        st = State.load(state_path)
        _check_pin(st, args)
        return _mutate(st, args, result)


def _check_pin(st: "State", args) -> None:
    want = getattr(args, "if_digest", None)
    if want and want != st.base_digest:
        raise safelib.StaleWriteError(
            f"{st.path} digest {(st.base_digest or '')[:12]} != --if-digest "
            f"{want[:12]} - the state changed since that read; nothing "
            "written")


def _mutate(st: "State", args, result: dict):

    if args.cmd == "set-phase":
        warnings = st.set_phase(args.phase, require_gates=not args.force)
        result["phase"] = args.phase
        if warnings:
            result["warnings"] = warnings
    elif args.cmd == "record-gate":
        gres = checklib.load_json(args.result, "gate result")
        g = st.record_gate(args.gate, gres, args.phase)
        result.update(gate=args.gate, status=g["status"],
                      attempts=g["attempts"])
    elif args.cmd == "artifact":
        st.set_artifact(args.name, args.path)
        result.update(name=args.name, path=args.path,
                      sha256=st.data["artifacts"][args.name]["sha256"])
    elif args.cmd == "edit":
        rec = st.apply_edit(args.edit_class, args.refs, args.note)
        result.update(edit=rec)
    elif args.cmd == "rehash":
        result.update(artifacts=st.rehash(args.names))
    elif args.cmd == "spawn":
        rec = st.record_spawn({
            "role": args.role, "model": args.model, "effort": args.effort,
            "phase": args.phase, "tokens": args.tokens,
            "cost_usd": args.cost_usd, "note": args.note})
        result.update(spawn=rec)
    elif args.cmd == "toolchain":
        rec = st.set_toolchain(args.image, args.versions_sha, args.pdk,
                               args.pdk_sha)
        result.update(toolchain=rec)
    elif args.cmd == "job-start":
        jid, rec = st.start_job(args.gate, args.pid, args.log)
        result.update(job=jid, **rec)
    elif args.cmd == "job-update":
        res = checklib.load_json(args.result, "job result") if args.result \
            else None
        rec = st.update_job(args.job, args.status, res)
        result.update(job=args.job, **rec)
    elif args.cmd == "holdout":
        result.update(holdout=st.record_holdout(args.written_by))
    elif args.cmd == "decision":
        st.add_decision(args.what, args.why, args.phase)
        result.update(what=args.what)
    elif args.cmd == "human":
        st.record_human(args.checkpoint, args.status, args.note)
        result.update(checkpoint=args.checkpoint, status=args.status)
    elif args.cmd == "issue":
        rec = st.update_issue(args.id, args.status, args.agent,
                              args.bump_attempts)
        result.update(issue=rec)
    elif args.cmd == "budget":
        remaining = st.budget(args.bpath, args.consume)
        result.update(path=args.bpath, remaining=remaining)
    elif args.cmd == "log":
        if not EVENT_RE.fullmatch(args.event or ""):
            raise CheckError(
                f"bad event name {args.event!r}: event names are machine "
                "keys matching [a-z][a-z0-9_-]{0,31} - put prose in "
                '--data {"msg": ...}')
        extra = json.loads(args.data) if args.data else {}
        if not isinstance(extra, dict):
            raise CheckError("--data must be a JSON object")
        if args.event == "spawn":
            result.update(spawn=st.record_spawn(extra))
        else:
            st._log(args.event, **extra)
        result.update(event=args.event)
    elif args.cmd == "snapshot":
        result.update(st.snapshot(args.label, args.files))
    elif args.cmd == "restore":
        result.update(st.restore(args.label))

    st.save()
    result["phase"] = st.data["phase"]
    result["digest"] = st.base_digest
    return result, args.out


def main(argv=None) -> int:
    checklib.utf8_stdout()
    try:
        payload, out = run(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001  (any error -> exit 2)
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}",
                          "remediation": str(exc)}))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
