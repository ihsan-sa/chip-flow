"""statelib.py - normalized artifact hashing + freshness computation.

Ported from /hwde's scripts/lib/statelib.py (docs/design.md 1.3), domain
swapped: KiCad's s-expression/gerber normalizers are gone (no `sexpr_no_uuid`,
no `gerber_design`); a chip-flow block's artifacts are HDL/SPICE text, spec
YAML/JSON, and test/reference directories, so the normalizer set is
text_eol, json_canonical (spec.yaml, config.json - anything YAML-or-JSON
shaped) and dir_text (rtl/, tb/, holdout/, ...). `gds_geometry` (a hardened
GDS hashed through klayout's python module, timestamps stripped) lands with
the milestone that first produces a GDS (M4/M9) - not needed while every
gate is a stub.

state.py owns the schema and CLI; this module owns everything that turns
files into design-content hashes and hashes into freshness verdicts.
Toolchain-free by design (pure venv: yaml) so freshness questions never
need the eda image.

Why normalized hashes: raw file hashes are NOT design fingerprints - a
regenerated file can carry timestamp/whitespace churn that says "different
file" for content that means the same design. Each artifact KIND therefore
hashes through a normalizer that removes exactly the churn that does not
change the design, and nothing else. Stored hashes are prefixed "<norm>:" so
a normalizer change can never false-match a hash computed under the old
rules.

The artifact kinds live in reference/invalidation.yaml (single source,
shared across skills - the same block layout, docs/design.md 1.4). The
per-gate input sets and the edit-class invalidation map are SKILL-scoped
there (gate names collide across /vde, /ade, /msde with different meanings,
docs/design.md 1.5) - every lookup here takes a `skill`.

Failure direction is always conservative: an unparsable input falls back to a
raw-bytes hash (only an identical file matches), an unknown gate hashes no
inputs (freshness "unknown", never "fresh"), a missing file hashes to None
(match only if it was also missing at record time).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

# engine/lib/statelib.py -> parents[0]=engine/lib, parents[1]=engine
DEFAULT_MAP = Path(__file__).resolve().parents[1] / "reference" / "invalidation.yaml"

_MAP_CACHE: dict[tuple[str, float], dict] = {}

SKILLS = ("vde", "ade", "msde")


# ---------------------------------------------------------------------------
# invalidation map
# ---------------------------------------------------------------------------
def load_map(path: Path | str | None = None) -> dict:
    """Parsed invalidation.yaml, validated. Cached on (path, mtime).

    Shape: {artifact_kinds: {kind: {path, norm}}, gate_inputs: {skill:
    {gate: [kind...]}}, edit_classes: {skill: {class: {mutates,
    stale_artifacts, gates, human_hold}}}}. artifact_kinds is FLAT - the
    same block layout (docs/design.md 1.4) is shared by every skill;
    gate_inputs and edit_classes are per-skill because gate names collide
    across skills with different meanings (drc, lvs, release, spec_lint)."""
    p = Path(path) if path else DEFAULT_MAP
    key = (str(p), p.stat().st_mtime)
    if key in _MAP_CACHE:
        return _MAP_CACHE[key]
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data.get("artifact_kinds"), dict) or not data["artifact_kinds"]:
        raise ValueError(f"{p}: missing/empty section 'artifact_kinds'")
    for section in ("gate_inputs", "edit_classes"):
        if not isinstance(data.get(section), dict) or not data[section]:
            raise ValueError(f"{p}: missing/empty section {section!r}")
        unknown_skills = set(data[section]) - set(SKILLS)
        if unknown_skills:
            raise ValueError(f"{p}: {section} names unknown skills "
                             f"{sorted(unknown_skills)}")
    for skill in data["gate_inputs"]:
        for gate, kinds in data["gate_inputs"][skill].items():
            unknown = [k for k in kinds if k not in data["artifact_kinds"]]
            if unknown:
                raise ValueError(f"{p}: {skill}.gate_inputs[{gate!r}] names "
                                 f"unknown artifact kinds {unknown}")
    for skill in data["edit_classes"]:
        gate_inputs = data["gate_inputs"].get(skill, {})
        for cls, ec in data["edit_classes"][skill].items():
            for field in ("mutates", "stale_artifacts", "gates", "human_hold"):
                if field not in ec:
                    raise ValueError(f"{p}: {skill}.{cls} lacks {field!r}")
            unknown = [k for k in ec["mutates"] + ec["stale_artifacts"]
                       if k not in data["artifact_kinds"]]
            if unknown:
                raise ValueError(f"{p}: {skill}.{cls} names unknown artifact "
                                 f"kinds {unknown}")
            unknown = [g for g in ec["gates"] if g not in gate_inputs]
            if unknown:
                raise ValueError(f"{p}: {skill}.{cls} names unknown gates "
                                 f"{unknown}")
    _MAP_CACHE[key] = data
    return data


# ---------------------------------------------------------------------------
# normalizers (each returns the BYTES to hash; None = use raw file bytes)
# ---------------------------------------------------------------------------
def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _eol(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _norm_text_eol(path: Path) -> bytes:
    return _eol(_text(path.read_bytes())).encode("utf-8")


def _norm_json_canonical(path: Path) -> bytes:
    """Parsed structured content, re-serialized with sorted keys - JSON or
    YAML (spec.yaml, config.json: a chip-flow spec is written in YAML, a
    LibreLane config in JSON; both are 'specs and configs', docs/design.md
    1.6, and yaml.safe_load reads JSON as a YAML subset)."""
    doc = yaml.safe_load(_text(path.read_bytes()))
    return json.dumps(doc, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def _norm_dir_text(path: Path) -> bytes:
    """All files under a directory, name-sorted, each EOL-normalized. Only
    defined for directories (rtl/, tb/, holdout/, ...); a file input raises
    so the caller's raw fallback takes over.

    `__pycache__` (and any stray `.pyc`/`.pyo`) is skipped: tb/ and holdout/
    are Python packages cocotb imports directly, and simply importing them
    once (no source edit at all) writes bytecode cache files under the very
    directory being hashed here - counted in, that would flip a gate's
    recorded input hash on its second run and mark a design gate stale for
    a reason that has nothing to do with the design."""
    if not path.is_dir():
        raise ValueError(f"{path} is not a directory")
    h_parts: list[bytes] = []
    for f in sorted(p for p in path.rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts
                    and p.suffix not in (".pyc", ".pyo")):
        rel = f.relative_to(path).as_posix()
        h_parts.append(rel.encode("utf-8") + b"\0"
                       + _eol(_text(f.read_bytes())).encode("utf-8") + b"\0")
    return b"dir\0" + b"".join(h_parts)


NORMALIZERS = {
    "text_eol": _norm_text_eol,
    "json_canonical": _norm_json_canonical,
    "dir_text": _norm_dir_text,
}

# Suffix -> normalizer for artifacts registered under non-standard names.
_SUFFIX_NORM = {
    ".json": "json_canonical",
    ".yaml": "json_canonical",
    ".yml": "json_canonical",
}


def norm_for_path(path: Path | str) -> str:
    p = Path(path)
    if p.is_dir():
        return "dir_text"
    return _SUFFIX_NORM.get(p.suffix.lower(), "text_eol")


def hash_artifact(path: Path | str, norm: str) -> str | None:
    """'<norm>:<sha256>' of the artifact's normalized content, None when the
    file/dir does not exist. Any normalizer failure (unparsable YAML/JSON,
    binary content) falls back to raw bytes under the 'raw' prefix - fail-safe:
    a raw hash only ever matches an identical file, so staleness can be
    over-reported but never missed."""
    p = Path(path)
    if not p.exists():
        return None
    fn = NORMALIZERS.get(norm)
    if fn is None:
        raise ValueError(f"unknown normalizer {norm!r}")
    try:
        out = fn(p)
    except Exception:
        if p.is_dir():  # unreadable dir member: hash the name list at least
            names = "\n".join(sorted(
                f.relative_to(p).as_posix() for f in p.rglob("*")))
            return "rawdir:" + hashlib.sha256(names.encode()).hexdigest()
        return "raw:" + hashlib.sha256(p.read_bytes()).hexdigest()
    return f"{norm}:{hashlib.sha256(out).hexdigest()}"


# ---------------------------------------------------------------------------
# workspace resolution + freshness
# ---------------------------------------------------------------------------
def find_workspace(input_file: Path | None,
                   explicit: str | None = None) -> Path | None:
    """The block workspace whose state.json a result/edit on `input_file`
    belongs in.

    An explicit --workspace wins and MUST hold a state.json (a typo that
    silently records nowhere is the bug this refusal exists to catch).
    Otherwise the input's own parents are walked - the pipeline always
    works on a file inside its workspace, so a caller that forgets the flag
    still records. A corpus input (evals/fixtures/..., a mutant, a scratch
    export) has no state.json above it and is left alone.
    """
    if explicit:
        ws = Path(explicit)
        if not (ws / "state.json").is_file():
            raise RuntimeError(f"--workspace {ws}: no state.json there")
        return ws
    if input_file is None:
        return None
    p = Path(input_file).resolve()
    for parent in list(p.parents)[:6]:
        if (parent / "state.json").is_file():
            return parent
    return None


# skills/msde/SKILL.md, "Run start": the nested workspaces take "block names
# `<name>` (digital - it is the tile) and `<name>_analog`". Never the
# directory's own name: layout_gen, analog LVS, pex_sim and top_harden name
# files after the block, so an `analog` block looks for netlist/analog.cir.
SPLIT_SUFFIX = {"digital": "", "analog": "_analog"}


def split_block_name(ws: Path | str) -> str | None:
    """The block name the msde split gives `ws`, or None when `ws` is not
    digital/ or analog/ directly under an msde workspace with a block."""
    ws = Path(ws)
    suffix = SPLIT_SUFFIX.get(ws.name)
    parent_state = ws.parent / "state.json"
    if suffix is None or not parent_state.is_file():
        return None
    try:
        parent = json.loads(parent_state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if parent.get("skill") != "msde" or not parent.get("block"):
        return None
    return parent["block"] + suffix


def kind_path(kind: str, imap: dict, registry: dict | None = None) -> str:
    """Workspace-relative path for a standard artifact kind. A registry
    entry registered under the kind's name overrides the default template -
    but ONLY when the entry's own `kind` field matches (an unqualified
    name-match would silently hash the wrong file and MISS real staleness)."""
    reg = (registry or {}).get(kind)
    if isinstance(reg, dict) and reg.get("path") and reg.get("kind") == kind:
        return reg["path"]
    return imap["artifact_kinds"][kind]["path"]


def hash_kind(ws: Path, kind: str, imap: dict,
              registry: dict | None = None) -> tuple[str, str | None]:
    """(workspace-relative path, hash-or-None) for a standard kind."""
    rel = kind_path(kind, imap, registry)
    norm = imap["artifact_kinds"][kind]["norm"]
    return rel, hash_artifact(ws / rel, norm)


def gate_input_hashes(ws: Path, skill: str, gate: str, imap: dict,
                      registry: dict | None = None) -> dict[str, str | None]:
    """{kind: hash|None} for every input the gate reads; {} for a gate the
    map does not know (freshness then reports it 'unknown', never 'fresh')."""
    kinds = (imap["gate_inputs"].get(skill) or {}).get(gate)
    if not kinds:
        return {}
    return {k: hash_kind(ws, k, imap, registry)[1] for k in kinds}


def gate_freshness(gate_entry: dict, current: dict[str, str | None]) -> dict:
    """Two-layer verdict for one recorded gate entry against current hashes.

    hash_valid: True (all recorded inputs match), False (>=1 differs),
                None (no recorded inputs - unknown gate or never recorded).
    fresh:      hash_valid is True AND no stale marks.
    """
    recorded = (gate_entry.get("last") or {}).get("inputs")
    marks = gate_entry.get("stale") or []
    if not isinstance(recorded, dict) or not recorded:
        return {"hash_valid": None, "changed_inputs": [],
                "stale_marks": marks, "fresh": False}
    changed = sorted(k for k, v in recorded.items() if current.get(k) != v)
    hash_valid = not changed
    return {"hash_valid": hash_valid, "changed_inputs": changed,
            "stale_marks": marks, "fresh": hash_valid and not marks}


def freshness_report(data: dict, ws: Path,
                     imap: dict | None = None) -> dict:
    """Full freshness view of a state.json v3 payload.

    artifacts: every registered entry + every standard kind mapped for a
    recorded gate - {path, kind, exists, registered, current, changed,
    marks}. gates: per recorded gate the gate_freshness verdict + recorded/
    current hashes. summary.human_hold_pending = max human_hold over all
    outstanding marks (0 when everything is clean).
    """
    imap = imap or load_map()
    skill = data.get("skill") or ""
    registry = data.get("artifacts") or {}

    gates_out: dict[str, dict] = {}
    for gname, gentry in (data.get("gates") or {}).items():
        current = gate_input_hashes(ws, skill, gname, imap, registry)
        verdict = gate_freshness(gentry, current)
        gates_out[gname] = {
            "status": gentry.get("status"),
            "recorded_inputs": (gentry.get("last") or {}).get("inputs"),
            "current_inputs": current,
            **verdict,
        }

    # artifacts: registered entries first, then any standard kind a recorded
    # gate reads that is not registered (visibility without registration)
    art_out: dict[str, dict] = {}
    for name, entry in registry.items():
        if not isinstance(entry, dict):        # pre-migration payload
            entry = {"path": str(entry)}
        rel = entry.get("path")
        kind = entry.get("kind")
        norm = (imap["artifact_kinds"][kind]["norm"]
                if kind in imap["artifact_kinds"]
                else norm_for_path(ws / rel) if rel else "text_eol")
        cur = hash_artifact(ws / rel, norm) if rel else None
        reg_sha = entry.get("sha256")
        art_out[name] = {
            "path": rel, "kind": kind,
            "exists": bool(rel) and (ws / rel).exists(),
            "registered": reg_sha, "current": cur,
            "changed": (reg_sha is not None or cur is not None)
                       and reg_sha != cur,
            "stale_marks": entry.get("stale") or [],
        }
    seen_kinds = {e.get("kind") for e in art_out.values()}
    for gname in gates_out:
        for kind in (imap["gate_inputs"].get(skill) or {}).get(gname, []):
            if kind in seen_kinds or kind in art_out:
                continue
            rel, cur = hash_kind(ws, kind, imap, registry)
            art_out[kind] = {
                "path": rel, "kind": kind, "exists": (ws / rel).exists(),
                "registered": None, "current": cur, "changed": False,
                "stale_marks": [],
            }
            seen_kinds.add(kind)

    holds = [m.get("human_hold", 0)
             for g in gates_out.values() for m in g["stale_marks"]]
    holds += [m.get("human_hold", 0)
              for a in art_out.values() for m in a["stale_marks"]]
    fresh = sorted(g for g, v in gates_out.items() if v["fresh"])
    stale = sorted(g for g, v in gates_out.items()
                   if not v["fresh"] and v["hash_valid"] is not None)
    unknown = sorted(g for g, v in gates_out.items()
                     if v["hash_valid"] is None)
    return {
        "skill": skill,
        "gates": gates_out,
        "artifacts": art_out,
        "summary": {"fresh": fresh, "stale": stale, "unknown": unknown,
                    "human_hold_pending": max(holds, default=0)},
    }
