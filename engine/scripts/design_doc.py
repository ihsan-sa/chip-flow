#!/usr/bin/env python
"""design_doc.py - a block's design document, as a house-style PDF.

    design_doc.py --workspace DIR [--file] [--no-build] [--out FILE]

Writes `doc/<block>_design.tex` in the workspace (and `doc/figures/`: a
block diagram spec for diagram-maker, and a layout render when the
workspace has a GDS), then builds it with the pdf-material-builder skill's
`scripts/build.sh` into `doc/<block>_design.pdf`. The skill is found at
$PDF_MATERIAL_BUILDER, else `~/.claude/skills/pdf-material-builder`.

What the document says, and where each part comes from:
  - what the block is: the first paragraphs of spec/spec.md (or brief/spec.md)
    and spec/spec.yaml's requirements, ports, clock, measures; interface.yaml
    for an msde block;
  - the architecture and why: a block diagram drawn from those ports or
    interface signals, and state.json's `decisions` (what, why, phase);
  - how it was verified: one row per gate gates.yaml owes the skill
    (attest.applicable_gates), numbers read from the gate's recorded result
    in state.json, and the fuller detail from reports/recorded/gate-<g>.json
    (gate.py writes it with every recorded run) while its recorded_ts is the
    gate's last ts; for a run recorded before that copy existed, from
    reports/gate-<g>.json only when that report agrees with the recorded
    result (same status, every recorded fact equal, written 0-120 s after
    the recorded ts) - a report left by another run is never read as the
    recorded one. A gate
    with no recorded result is a row that says "not run", never left out;
    an msde block adds a section for each nested side;
  - PPA: synth area, timing slack per corner, analog measures against their
    bounds; what no gate measured is said to be unmeasured;
  - the release record: attest.verify on reports/checks.json, H2, waivers.
No number in the document is typed by hand: every one is read from those
files when the document is built.

Filing. A released design's document is filed in the owner's document
register, project $CHIPFLOW_DOC_PROJECT (default "004", "Chip design"), by
build.sh's own DOC_PROJECT/DOC_TITLE route (cc-docs number, then cc-docs
file), with the .tex path as the source so a rebuild files as the next
revision. It files only with --file AND when filing_refusal() finds nothing:
  - the workspace is released (attest.verify is valid);
  - it is not a nested side of an msde block (the msde block's document
    covers both sides);
  - it is not inside a chip-flow checkout (the corpus, evals, ladder and
    shakedown runs all live there; a real block lives in its project's own
    repo, docs/design.md 1.4);
  - it is not a test run: not under the temp directory, no PYTEST_CURRENT_TEST;
  - CHIPFLOW_DOC_NO_FILE is not set.
Otherwise the document is built as a draft, unnumbered, and the JSON says
why it was not filed. Without --file nothing is ever filed.

JSON out; exit 0 built (filed or a draft), 2 error with a remediation (no
state.json, the builder skill missing, a LaTeX build that failed).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import attest as attest_mod  # noqa: E402

SCRIPT = "design_doc"
DEFAULT_PROJECT = "004"
SKILL_NAMES = {"vde": "digital", "ade": "analog", "msde": "mixed-signal"}
GATE_WHAT = {
    "spec_lint": "spec is checkable", "lint": "Verilator lint",
    "sim": "cocotb simulation", "holdout": "held-out tests",
    "mutate": "mutation score", "formal": "SymbiYosys proof",
    "cover": "code coverage", "synth": "Yosys synthesis",
    "harden": "LibreLane to GDS", "timing": "OpenSTA signoff",
    "drc": "magic + klayout DRC", "lvs": "netgen LVS",
    "glsim": "gate-level sim", "precheck": "Tiny Tapeout precheck",
    "netlist_lint": "ngspice dry run", "sim_tt": "typical corner",
    "sim_pvt": "PVT corners", "bench_strength": "bench mutants",
    "mc": "Monte Carlo", "pex_sim": "post-layout sim",
    "split": "interface agrees", "cosim": "mixed-signal cosim",
    "top_harden": "top-level harden", "top_drc": "top-level DRC",
    "top_lvs": "top-level LVS",
}


class DocError(Exception):
    def __init__(self, msg: str, remediation: str):
        super().__init__(msg)
        self.remediation = remediation


# ---- reading the workspace ------------------------------------------------

def load_yaml(path: Path):
    if not path.is_file():
        return None
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_state(ws: Path) -> dict:
    if not (ws / "state.json").is_file():
        raise DocError(f"no state.json in {ws}",
                       "point --workspace at a block made by state.py init")
    return checklib.load_json(ws / "state.json", "state.json")


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def report_agrees(report: dict, last: dict, mtime: float | None = None) -> bool:
    """A report on disk counts as the recorded result only when it says the
    same: the same status, every fact state.json kept equal, its input
    digest one of the recorded inputs, and - since a later or earlier run
    overwrites the same file - written when the recorded run finished:
    gate.py records `ts` (local, whole seconds) just before the caller
    writes --out, so the file's mtime falls 0-120 s after it, compared in
    whole seconds."""
    if report.get("status") != last.get("status"):
        return False
    rfacts = report.get("facts") or {}
    for k, v in (last.get("facts") or {}).items():
        if rfacts.get(k) != v:
            return False
    digest = report.get("input_digest")
    if digest is not None and digest not in (last.get("inputs") or {}).values():
        return False
    if mtime is not None:
        from datetime import datetime
        try:
            ts = datetime.fromisoformat(str(last.get("ts"))).timestamp()
        except ValueError:
            return False
        if not -2 <= int(mtime) - int(ts) <= 120:
            return False
    return True


def gate_record(ws: Path, data: dict, gate: str) -> dict:
    """{gate, ran, status, ts, attempts, facts, detail} for one gate. The
    facts are state.json's, widened by the recorded run's whole result:
    reports/recorded/gate-<g>.json when its recorded_ts is the gate's last
    ts (gate.py writes it), else reports/gate-<g>.json when report_agrees()."""
    g = (data.get("gates") or {}).get(gate) or {}
    last = g.get("last") or {}
    if not last:
        na = attest_mod.not_applicable_reason(ws, data.get("skill"), gate)
        return {"gate": gate, "ran": False, "na": na}
    facts = dict(last.get("facts") or {})
    detail = None
    rec = _load(ws / "reports" / "recorded" / f"gate-{gate}.json")
    rpath = ws / "reports" / f"gate-{gate}.json"
    if rec and rec.get("recorded_ts") == last.get("ts") and report_agrees(rec, last):
        facts, detail = {**(rec.get("facts") or {}), **facts}, "recorded"
    elif rpath.is_file():
        report = _load(rpath)
        if report_agrees(report, last, rpath.stat().st_mtime):
            facts, detail = {**(report.get("facts") or {}), **facts}, "report"
    return {"gate": gate, "ran": True, "status": last.get("status"),
            "ts": last.get("ts"), "attempts": g.get("attempts"),
            "stale": bool(g.get("stale")), "facts": facts,
            "total": last.get("total"), "failing_count": last.get("failing_count"),
            "detail": detail}


def spec_intro(ws: Path) -> str:
    """spec.md's paragraphs before its first `## ` heading, title dropped."""
    for p in (ws / "spec" / "spec.md", ws / "brief" / "spec.md"):
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            head = re.split(r"(?m)^## ", text, maxsplit=1)[0]
            return re.sub(r"(?m)^# .*\n", "", head).strip()
    return ""


def find_gds(ws: Path, data: dict) -> Path | None:
    arts = data.get("artifacts") or {}
    for kind in ("top_gds", "gds", "analog_layout", "layout"):
        a = arts.get(kind)
        if not a:
            continue
        p = ws / a["path"]
        cands = [p] if p.is_file() else sorted(p.glob("*.gds")) if p.is_dir() else []
        if cands:
            return cands[0]
    for pat in ("harden/runs/*/final/gds/*.gds", "top/harden/runs/*/final/gds/*.gds",
                "layout/*.gds"):
        cands = sorted(ws.glob(pat))
        if cands:
            return cands[0]
    return None


# ---- filing ---------------------------------------------------------------

def in_chipflow_checkout(ws: Path) -> Path | None:
    for d in [ws, *ws.parents]:
        if (d / "engine" / "scripts" / "task_router.py").is_file() and \
                (d / "skills" / "vde" / "SKILL.md").is_file():
            return d
    return None


def filing_refusal(ws: Path, data: dict, env) -> str | None:
    """Why this workspace's document must not be filed; None when it may.
    The brief: "only a released design's document is filed ... drafts,
    test runs, corpus/eval/ladder runs and pytest fixtures must never file"."""
    ws = ws.resolve()
    if env.get("CHIPFLOW_DOC_NO_FILE"):
        return "CHIPFLOW_DOC_NO_FILE is set"
    if env.get("PYTEST_CURRENT_TEST"):
        return "a test run (PYTEST_CURRENT_TEST is set)"
    tmp = Path(tempfile.gettempdir()).resolve()
    if ws == tmp or tmp in ws.parents:
        return f"a test run (the workspace is under {tmp})"
    checkout = in_chipflow_checkout(ws)
    if checkout is not None:
        return ("a corpus, eval or ladder run (the workspace is inside the "
                "chip-flow checkout at " + str(checkout) + ")")
    parent = ws.parent / "state.json"
    if parent.is_file():
        pdata = checklib.load_json(parent, "state.json")
        if pdata.get("skill") == "msde":
            return ("a nested side of the msde block at " + str(ws.parent) +
                    "; that block's document covers it")
    verdict = attest_mod.verify(ws)
    if not verdict.get("valid"):
        return "not released: " + str(verdict.get("reason"))
    return None


# ---- LaTeX ---------------------------------------------------------------

_TEX = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}", "<": r"\textless{}", ">": r"\textgreater{}"}


def tex(s) -> str:
    return "".join(_TEX.get(c, c) for c in str(s))


def md(s: str) -> str:
    """The little markdown a spec paragraph uses: `code` and **bold**."""
    out = []
    for i, part in enumerate(re.split(r"`([^`]*)`", s)):
        if i % 2:
            out.append(r"\texttt{" + tex(part) + "}")
        else:
            t = tex(part)
            out.append(re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", t))
    return "".join(out)


def num(v, unit: str = "") -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        a = abs(v)
        if a != 0 and (a < 1e-3 or a >= 1e5):
            m, e = f"{v:.3e}".split("e")
            return f"{m}e{int(e)}{unit}"
        return f"{v:.4g}{unit}"
    return f"{v}{unit}"


def pct(v) -> str:
    return f"{float(v) * 100:.1f}\\%" if v is not None else "?"


def gate_numbers(r: dict) -> str:
    """The numbers one gate's recorded result carries, as a short line."""
    f = r.get("facts") or {}
    g = r["gate"]
    parts: list[str] = []
    if g in ("sim", "holdout") and "tests_passed" in f:
        run = f.get("tests_run")
        n = len(run) if isinstance(run, list) else run
        parts.append(f"{f['tests_passed']}" + (f"/{n}" if n is not None else "")
                     + " tests pass")
    elif g in ("mutate", "bench_strength") and "total_mutants" in f:
        scored = f.get("scored_mutants", f["total_mutants"] - f.get("equivalent", 0))
        parts.append(f"{f.get('killed')}/{scored} killed")
        if "kill_rate" in f:
            parts.append("kill rate " + pct(f["kill_rate"]))
        if f.get("equivalent"):
            parts.append(f"{f['equivalent']} ruled equivalent")
        if f.get("below_spread"):
            parts.append(f"{f['below_spread']} below spread")
        if f.get("survived") and g == "bench_strength":
            parts.append(f"{f['survived']} survived")
    elif g == "formal":
        pr = f.get("proven")
        if isinstance(pr, list):
            parts.append(f"{len(pr)} properties proven")
        if "depth" in f:
            parts.append(f"depth {f['depth']}")
        for k in ("smt_status", "pdr_status"):
            if k in f:
                parts.append(f"{k.split('_')[0]} {f[k]}")
        cp = f.get("cover_points")
        if isinstance(cp, list):
            parts.append(f"{len(cp)} covers reached")
    elif g == "cover" and "line_pct" in f:
        parts.append(f"line {f['line_pct']:.1f}\\% ({f.get('line_covered')}/{f.get('line_total')})")
        parts.append(f"toggle {f.get('toggle_pct', 0):.1f}\\% ({f.get('toggle_covered')}/{f.get('toggle_total')})")
    elif g == "synth" and "area" in f:
        parts.append(f"area {num(f['area'])} \\textmu m\\textsuperscript{{2}}")
        if isinstance(f.get("cells"), dict):
            parts.append(f"{sum(f['cells'].values())} cells")
    elif g == "timing" and isinstance(f.get("corners"), dict):
        s, h = worst_slack(f["corners"])
        parts.append(f"worst setup slack {num(s)} ns, hold {num(h)} ns over {len(f['corners'])} corners")
    elif g in ("drc", "top_drc") and "magic_count" in f:
        parts.append(f"magic {f['magic_count']}, klayout {f.get('klayout_count')} violations")
    elif g in ("lvs", "top_lvs"):
        if "matched" in f:
            parts.append("match" if f["matched"] else "mismatch")
        if "macro_fets_extracted" in f:
            parts.append(f"{f['macro_fets_extracted']}/{f.get('macro_fets_wanted')} macro FETs")
    elif g == "precheck" and "checks_run" in f:
        parts.append(f"{f['checks_run']} checks run")
    elif g in ("sim_tt", "sim_pvt", "pex_sim") and f.get("corners"):
        parts.append(f"{len(f['corners'])} corners: " + ", ".join(map(str, f["corners"])))
    elif g == "split" and isinstance(f.get("signals"), list):
        parts.append(f"{len(f['signals'])} crossing signals agree")
    elif g == "cosim" and isinstance(f.get("measures"), dict):
        m = f["measures"]
        if "count" in m and "expected_count" in m:
            parts.append(f"count {m['count']} vs {num(m['expected_count'])} expected")
        if "f_osc_hz" in m:
            parts.append(f"$f_{{osc}}$ {num(m['f_osc_hz'])} Hz")
    if "wall_s" in f and not parts:
        parts.append(f"{f['wall_s']:.0f} s")
    if not parts:
        scal = [(k, v) for k, v in f.items() if isinstance(v, (int, float))
                and not isinstance(v, bool)]
        parts = [f"{tex(k)} {tex(num(v))}" for k, v in scal[:3]]
    if not parts and r.get("total") is not None:
        parts.append(f"{r['total']} findings, {r.get('failing_count', 0)} failing")
    return "; ".join(parts) if parts else "no numbers recorded"


def worst_slack(corners: dict) -> tuple:
    s = [c.get("setup_ws") for c in corners.values() if c.get("setup_ws") is not None]
    h = [c.get("hold_ws") for c in corners.values() if c.get("hold_ws") is not None]
    return (min(s) if s else None, min(h) if h else None)


def gate_table(rows: list[dict], label: str) -> str:
    out = [r"\needspace{8\baselineskip}\begin{hsblock}\hslabel{" + label + r"}\par\vspace{8pt}",
           r"\begin{xltabular}{\linewidth}{@{}L{0.18\linewidth}L{0.15\linewidth}L{0.1\linewidth}Y@{}}",
           r"\hstoprule",
           r"\hshead{Gate} & \hshead{Checks} & \hshead{Result} & \hshead{Recorded numbers}\\ \hline"]
    for r in rows:
        name = r"\texttt{\small " + tex(r["gate"]) + "}"
        what = tex(GATE_WHAT.get(r["gate"], ""))
        if not r["ran"] and r.get("na"):
            out.append(f"{name} & {what} & n/a & {tex(r['na'])}\\\\ \\hline")
            continue
        if not r["ran"]:
            out.append(f"{name} & {what} & \\textcolor{{accent}}{{not run}} & no recorded result\\\\ \\hline")
            continue
        status = tex(r["status"]) + (" (stale)" if r.get("stale") else "")
        if r["status"] != "pass" or r.get("stale"):
            status = r"\textcolor{accent}{" + status + "}"
        when = tex((r.get("ts") or "")[:16].replace("T", " "))
        out.append(f"{name} & {what} & {status} & {gate_numbers(r)}"
                   f" \\newline {{\\color{{inkseventy}}\\footnotesize {when}}}\\\\ \\hline")
    out += [r"\end{xltabular}", r"\end{hsblock}", ""]
    return "\n".join(out)


def detail_note(rows: list[dict]) -> str:
    thin = [r["gate"] for r in rows if r["ran"] and not r.get("detail")]
    if not thin:
        return ""
    return ("For " + ", ".join(r"\texttt{" + tex(g) + "}" for g in thin) +
            " the full result of the run on record was not kept, so the row gives "
            "only the numbers state.json recorded with it.\n\n")


def missing_callout(rows: list[dict], where: str) -> str:
    missing = [r["gate"] for r in rows if not r["ran"] and not r.get("na")]
    failing = [r["gate"] for r in rows if r["ran"] and (r["status"] != "pass" or r.get("stale"))]
    if not missing and not failing:
        return ""
    body = []
    if missing:
        body.append(f"{len(missing)} of the {len(rows)} gates {where} owes have no recorded "
                    "result, so this document has no numbers for them: " +
                    ", ".join(r"\texttt{" + tex(g) + "}" for g in missing) + ".")
    if failing:
        body.append("Recorded as failing or stale: " +
                    ", ".join(r"\texttt{" + tex(g) + "}" for g in failing) + ".")
    return ("\\begin{hscallout}{What was not verified}\n" + " ".join(body) +
            "\n\\end{hscallout}\n")


# ---- sections -------------------------------------------------------------

def requirements_table(spec: dict) -> str:
    reqs = spec.get("requirements") or []
    if not reqs:
        return ""
    out = [r"\needspace{8\baselineskip}\begin{hsblock}\hslabel{Requirements, from spec/spec.yaml}\par\vspace{8pt}",
           r"\begin{xltabular}{\linewidth}{@{}L{0.24\linewidth}L{0.09\linewidth}Y@{}}",
           r"\hstoprule", r"\hshead{Id} & \hshead{Check} & \hshead{Requirement}\\ \hline"]
    for r in reqs:
        text = " ".join(str(r.get("text", "")).split())
        out.append(r"\texttt{\small " + tex(r.get("id")) + "} & " + tex(r.get("check", "")) +
                   " & " + tex(text) + r"\\ \hline")
    out += [r"\end{xltabular}", r"\end{hsblock}", ""]
    return "\n".join(out)


def ports_line(spec: dict) -> str:
    ports = spec.get("ports") or {}
    if not ports:
        return ""
    items = []
    for name, p in ports.items():
        p = p or {}
        w = p.get("width", 1)
        items.append(r"\texttt{" + tex(name) + (f"[{w - 1}:0]" if w and w > 1 else "") +
                     "} " + tex(p.get("dir", "")))
    clk = spec.get("clock") or {}
    line = "Ports: " + ", ".join(items) + "."
    if clk.get("period_ns"):
        line += f" Clock period {num(clk['period_ns'])} ns" + \
                (f" ({num(1e3 / clk['period_ns'])} MHz)." if clk['period_ns'] else ".")
    return line + "\n\n"


def scored_bounds(ws: Path, spec: dict) -> tuple[list[dict], str]:
    """The bounds sim_pvt scores: the bench's tb/*.bounds.json sidecars
    (simlib.load_bounds), else spec.yaml's measures."""
    out = []
    for p in sorted((ws / "tb").glob("*.bounds.json")):
        data = _load(p)
        if isinstance(data, list):
            out += [b for b in data if isinstance(b, dict) and b.get("measure")]
    if out:
        return out, "tb/*.bounds.json, the bounds the gate scores"
    return ([{"measure": m.get("name"), **(m.get("bounds") or {}),
              "corners": m.get("corners", "all")}
             for m in spec.get("measures") or []], "spec/spec.yaml")


def measures_table(ws: Path, spec: dict, pvt: dict | None) -> str:
    bounds, source = scored_bounds(ws, spec)
    if not bounds:
        return ""
    kept = bool(pvt and pvt.get("ran") and pvt.get("detail"))
    results = (pvt or {}).get("facts", {}).get("results") or [] if kept else []
    out = []
    if pvt and pvt.get("ran") and not kept:
        out.append("The sim\\_pvt run on record kept no per-measure values (its full "
                   "result was not saved with it), so the table gives the bounds it "
                   "passed against and no measured numbers.\n")
    out += [r"\needspace{8\baselineskip}\begin{hsblock}\hslabel{Analog measures, bounds from " + tex(source) + r"}\par\vspace{8pt}",
            r"\begin{xltabular}{\linewidth}{@{}L{0.24\linewidth}L{0.2\linewidth}L{0.26\linewidth}Y@{}}",
            r"\hstoprule", r"\hshead{Measure} & \hshead{Bound} & \hshead{Measured, min to max} & \hshead{Corners}\\ \hline"]
    for m in bounds:
        name = m.get("measure")
        # YAML 1.1 reads 1.3482e6 (no sign in the exponent) as a string
        b = {k: float(m[k]) for k in ("min", "max") if m.get(k) is not None}
        bound = f"{num(b['min'])} to {num(b['max'])}" if len(b) == 2 else \
            r"$\geq$ " + num(b["min"]) if "min" in b else \
            r"$\leq$ " + num(b["max"]) if "max" in b else "none"
        want = m.get("corners", "all")
        want = set(want) if isinstance(want, list) else None  # "all"
        vals = [(r.get("corner"), (r.get("measures") or {}).get(name)) for r in results
                if want is None or r.get("corner") in want]
        vals = [(c, v) for c, v in vals if isinstance(v, (int, float))]
        scope = "all" if want is None else ", ".join(tex(c) for c in sorted(want))
        if vals:
            lo, hi = min(v for _, v in vals), max(v for _, v in vals)
            meas = num(lo) if lo == hi else f"{num(lo)} to {num(hi)}"
            if ("min" in b and lo < b["min"]) or ("max" in b and hi > b["max"]):
                meas = r"\textcolor{accent}{" + meas + " (out of bound)}"
            scope = ", ".join(tex(c) for c, _ in vals)
        elif kept:
            meas = r"\textcolor{accent}{not measured}"
        else:
            meas = "not kept"
        out.append(r"\texttt{\small " + tex(name) + "} & " + bound + " & " + meas + " & " + scope + r"\\ \hline")
    out += [r"\end{xltabular}", r"\end{hsblock}", ""]
    return "\n".join(out)


def ppa_section(recs: dict, spec: dict, skill: str) -> str:
    out = [r"\subsection{Performance, power and area}"]
    synth, timing = recs.get("synth"), recs.get("timing")
    lines = []
    if skill == "vde":
        if synth and synth["ran"] and "area" in synth["facts"]:
            lines.append(f"Synthesised cell area is {num(synth['facts']['area'])} "
                         r"\textmu m\textsuperscript{2} on the GF180 standard cells (synth gate).")
        else:
            lines.append(r"Area: \textcolor{accent}{no synth result is recorded}.")
        if timing and timing["ran"] and isinstance(timing["facts"].get("corners"), dict):
            s, h = worst_slack(timing["facts"]["corners"])
            per = (spec.get("clock") or {}).get("period_ns")
            lines.append(f"At a {num(per)} ns clock the worst setup slack is {num(s)} ns and the "
                         f"worst hold slack {num(h)} ns, over {len(timing['facts']['corners'])} "
                         "signoff corners (timing gate).")
        else:
            lines.append(r"Timing: \textcolor{accent}{no timing result is recorded}.")
    power = [r for r in recs.values() if r.get("ran") and
             any("power" in k for k in (r.get("facts") or {}))]
    if not power:
        lines.append("No gate in this flow measures power, so this document gives no power figure.")
    out.append(" ".join(lines) + "\n")
    if skill == "vde" and timing and timing["ran"] and isinstance(timing["facts"].get("corners"), dict):
        out.append(r"\needspace{8\baselineskip}\begin{hsblock}\hslabel{Timing slack per corner, ns}\par\vspace{8pt}")
        out.append(r"\begin{xltabular}{\linewidth}{@{}Y R{0.2\linewidth}R{0.2\linewidth}@{}}")
        out.append(r"\hstoprule \hshead{Corner} & \hshead{Setup} & \hshead{Hold}\\ \hline")
        for c, v in sorted(timing["facts"]["corners"].items()):
            out.append(r"\texttt{\small " + tex(c) + "} & " + num(v.get("setup_ws")) + " & " +
                       num(v.get("hold_ws")) + r"\\ \hline")
        out.append(r"\end{xltabular}\end{hsblock}")
    return "\n".join(out) + "\n"


def decisions_list(data: dict) -> str:
    ds = data.get("decisions") or []
    if not ds:
        return "No design decision is recorded in state.json.\n\n"
    out = [r"\begin{itemize}"]
    for d in ds:
        out.append(r"\item \textbf{" + tex(d.get("what", "")) + "} " + tex(d.get("why", "")) +
                   r" {\color{inkseventy}\footnotesize (" + tex(d.get("phase", "")) + ")}")
    out.append(r"\end{itemize}")
    return "\n".join(out) + "\n"


# ---- figures --------------------------------------------------------------

def _portsub(names: list[str]) -> list[str]:
    s = ", ".join(names)
    return [s] if len(s) <= 34 else [", ".join(names[:len(names) // 2]) + ",",
                                      ", ".join(names[len(names) // 2:])]


def block_diagram(ws: Path, data: dict, spec: dict) -> dict:
    block = data.get("block", ws.name)
    skill = data.get("skill")
    if skill == "msde":
        iface = load_yaml(ws / "interface.yaml") or {}
        sigs = iface.get("signals") or []
        dname = (load_yaml(ws / "digital" / "spec" / "spec.yaml") or {}).get("top", "digital")
        aname = (load_yaml(ws / "analog" / "spec" / "spec.yaml") or {}).get("top", "analog")
        nodes = [
            {"id": "pins", "title": "Tile pins", "sub": ["ui_in, uo_out, uio", "clk, rst_n"], "role": "io", "col": 0, "row": 0.5},
            {"id": "d", "title": "Digital side", "sub": [str(dname), "/vde"], "role": "work", "col": 1, "row": 0.5},
            {"id": "a", "title": "Analog macro", "sub": [str(aname), "/ade"], "role": "work", "col": 2, "row": 0.5},
        ]
        edges = [{"from": "pins", "to": "d", "both": True, "label": "tile I/O"}]
        d2a = [s["name"] for s in sigs if s.get("direction") == "d2a"]
        a2d = [s["name"] for s in sigs if s.get("direction") == "a2d"]
        if d2a:
            edges.append({"from": "d", "to": "a", "label": ", ".join(d2a), "mono": True, "fromAt": 0.3, "toAt": 0.3})
        if a2d:
            edges.append({"from": "a", "to": "d", "label": ", ".join(a2d), "mono": True, "fromAt": 0.7, "toAt": 0.7})
        return {"type": "blocks", "canvas": 553,
                "alt": f"{block}: the digital side and the analog macro and the signals between them",
                "nodes": nodes, "edges": edges,
                "zones": [{"label": f"tt_um_{block}", "members": ["d", "a"], "fence": True}]}
    ports = spec.get("ports") or {}
    ins = [n for n, p in ports.items() if (p or {}).get("dir") == "input"]
    outs = [n for n, p in ports.items() if (p or {}).get("dir") in ("output", "inout")]
    top = spec.get("top", block)
    sub = ["/" + str(skill), SKILL_NAMES.get(skill, "")]
    if skill == "ade" and spec.get("supply"):
        sub = [", ".join(f"{k} {v} V" for k, v in spec["supply"].items()), "/ade"]
    nodes = [{"id": "top", "title": str(top), "sub": sub, "role": "work", "col": 1, "row": 0}]
    edges = []
    if ins:
        nodes.append({"id": "in", "title": "Inputs", "sub": _portsub(ins), "role": "io", "col": 0, "row": 0})
        edges.append({"from": "in", "to": "top"})
    if outs:
        nodes.append({"id": "out", "title": "Outputs", "sub": _portsub(outs), "role": "io", "col": 2, "row": 0})
        edges.append({"from": "top", "to": "out"})
    return {"type": "blocks", "canvas": 553, "alt": f"{top}: its ports in and out",
            "nodes": nodes, "edges": edges}


RENDER = r"""
import sys, klayout.lay as lay
v = lay.LayoutView()
v.set_config("background-color", "#ffffff")
v.set_config("grid-visible", "false")
v.set_config("text-visible", "false")
v.load_layout(sys.argv[1], True)
v.max_hier()
v.zoom_fit()
cv = v.active_cellview()
b = cv.cell.dbbox()
w = 1600
h = max(200, int(w * b.height() / max(b.width(), 1e-9)))
v.save_image(sys.argv[2], w, min(h, 2000))
"""


def render_layout(gds: Path, png: Path) -> str | None:
    """Renders gds to png with klayout; the reason when it could not."""
    try:
        import klayout.lay  # noqa: F401
    except ImportError:
        return "klayout's Python layout view is not in this interpreter (run through bin/eda)"
    script = png.with_suffix(".render.py")
    script.write_text(RENDER, encoding="utf-8")
    try:
        p = subprocess.run([sys.executable, str(script), str(gds), str(png)],
                           capture_output=True, text=True, timeout=300)
    finally:
        script.unlink(missing_ok=True)
    if p.returncode != 0 or not png.is_file():
        return "klayout could not render it: " + (p.stderr or p.stdout).strip()[-200:]
    return None


# ---- the document -----------------------------------------------------------

def side_rows(ws: Path, data: dict) -> tuple[list[dict], dict]:
    gates = attest_mod.applicable_gates(data["skill"])
    rows = [gate_record(ws, data, g) for g in gates]
    return rows, {r["gate"]: r for r in rows}


def stat_row(rows: list[dict], recs: dict, skill: str) -> str:
    passed = sum(1 for r in rows if r["ran"] and r["status"] == "pass" and not r.get("stale"))
    stats = [(f"{passed}/{len(rows)}", "gates with a fresh recorded pass")]

    def f(g, k):
        r = recs.get(g)
        return (r or {}).get("facts", {}).get(k) if r and r["ran"] else None
    if skill == "vde":
        if f("mutate", "kill_rate") is not None:
            stats.append((pct(f("mutate", "kill_rate")), "mutants killed by the testbench"))
        if f("cover", "line_pct") is not None:
            stats.append((f"{f('cover', 'line_pct'):.0f}\\%", "RTL lines covered"))
        if f("timing", "corners"):
            s, _ = worst_slack(f("timing", "corners"))
            stats.append((num(s), "ns worst setup slack"))
    elif skill == "ade":
        if f("sim_pvt", "corners"):
            stats.append((str(len(f("sim_pvt", "corners"))), "PVT corners inside every bound"))
        if f("bench_strength", "killed") is not None:
            stats.append((f"{f('bench_strength', 'killed')}/{f('bench_strength', 'total_mutants')}",
                          "device mutants the bench caught"))
    elif skill == "msde":
        m = (f("cosim", "measures") or {})
        if "count" in m:
            stats.append((str(m["count"]), f"counts latched in cosim, {num(m.get('expected_count'))} expected"))
        if f("top_drc", "magic_count") is not None:
            stats.append((str(f("top_drc", "magic_count") + (f("top_drc", "klayout_count") or 0)),
                          "top-level DRC violations"))
    stats = stats[:4]
    body = "\n".join(r"\hsstat{" + v + "}{" + c + "}" for v, c in stats)
    return f"\\begin{{hsstatrow}}[{max(2, len(stats))}]\n{body}\n\\end{{hsstatrow}}\n"


def release_text(ws: Path, data: dict) -> tuple[str, bool]:
    v = attest_mod.verify(ws)
    h2 = ((data.get("human") or {}).get("H2") or {})
    waivers = attest_mod.load_waivers(ws) if (ws / "spec").exists() else []
    if v.get("valid"):
        s = ("Released. reports/checks.json verifies against the current files and gate "
             "records (attestation " + r"\texttt{" + tex(v["attestation_sha256"][:12]) + "}).")
    else:
        s = r"\textcolor{accent}{Not released.} " + tex(v.get("reason", "")) + "."
    if h2.get("status"):
        s += " Human sign-off H2: " + tex(h2["status"]) + (" on " + tex(h2.get("ts", "")[:10]) if h2.get("ts") else "") + "."
    else:
        s += " No human sign-off (H2) is recorded."
    s += f" {len(waivers)} waiver{'s' if len(waivers) != 1 else ''} recorded."
    return s, bool(v.get("valid"))


def build_tex(ws: Path, data: dict, figs: dict) -> tuple[str, dict]:
    block = data.get("block", ws.name)
    skill = data["skill"]
    spec = load_yaml(ws / "spec" / "spec.yaml") or {}
    rows, recs = side_rows(ws, data)
    rel, released = release_text(ws, data)
    from datetime import date
    month = date.today().strftime("%B %Y")
    lead = ("How " + tex(block) + " was designed and verified, with every number read "
            "from its recorded gate results." + ("" if released else " A draft: the block is not released."))
    t = [r"\input{preamble.tex}", r"\hsslug{" + tex(block) + " design document}", r"\begin{document}",
         r"\hstitleblock{chip-flow /" + tex(skill) + r" \textperiodcentered\ " + month + "}{" +
         tex(block) + "}{" + lead + "}", ""]
    t.append(r"\section{What it is}")
    intro = spec_intro(ws)
    for para in [p for p in re.split(r"\n\s*\n", intro) if p.strip()]:
        t.append(md(" ".join(para.split())) + "\n")
    if not intro:
        t.append(r"\textcolor{accent}{No spec.md was found in spec/ or brief/.}" + "\n")
    t.append(ports_line(spec))
    t.append(requirements_table(spec))
    if skill == "msde":
        iface = load_yaml(ws / "interface.yaml") or {}
        sigs = iface.get("signals") or []
        if sigs:
            t.append(r"\needspace{8\baselineskip}\begin{hsblock}\hslabel{Signals crossing the split, from interface.yaml}\par\vspace{8pt}")
            t.append(r"\begin{xltabular}{\linewidth}{@{}L{0.14\linewidth}L{0.08\linewidth}L{0.13\linewidth}L{0.13\linewidth}Y@{}}")
            t.append(r"\hstoprule \hshead{Signal} & \hshead{Way} & \hshead{Level} & \hshead{Domain} & \hshead{Load}\\ \hline")
            for s in sigs:
                t.append(r"\texttt{" + tex(s.get("name")) + "} & " + tex(s.get("direction", "")) + " & " +
                         tex(s.get("level", "")) + " & " + tex(s.get("domain", "")) + " & " +
                         tex(s.get("load", "")) + r"\\ \hline")
            t.append(r"\end{xltabular}\end{hsblock}" + "\n")
    t.append(r"\section{Architecture, and why}")
    if figs.get("block"):
        t.append(r"\begin{hsfigure}{Figure 1 / block diagram}{Drawn from " +
                 ("interface.yaml" if skill == "msde" else "the ports in spec/spec.yaml") +
                 r".}" + "\n" + r"\hsdiagram{figures/block}" + "\n" + r"\end{hsfigure}")
    t.append("The decisions recorded while the block was designed, each with its reason:\n")
    t.append(decisions_list(data))
    t.append(r"\section{How it was verified}")
    t.append(stat_row(rows, recs, skill))
    t.append(gate_table(rows, f"Table / the {tex(skill)} gates, as recorded"))
    t.append(detail_note(rows))
    t.append(missing_callout(rows, "this block"))
    if skill == "ade":
        t.append(measures_table(ws, spec, recs.get("sim_pvt")))
    sides = {}
    if skill == "msde":
        for side, want in (("digital", "vde"), ("analog", "ade")):
            sub = ws / side
            if not (sub / "state.json").is_file():
                t.append(r"\subsection{The " + side + r" side}" + "\n" +
                         r"\textcolor{accent}{No nested workspace at " + side + r"/, so none of its gates has a result here.}" + "\n")
                sides[side] = None
                continue
            sdata = load_state(sub)
            srows, srecs = side_rows(sub, sdata)
            sspec = load_yaml(sub / "spec" / "spec.yaml") or {}
            srel, sok = release_text(sub, sdata)
            t.append(r"\subsection{The " + side + " side, " + r"\texttt{" + tex(sspec.get("top", side)) + "}}")
            t.append(srel + "\n")
            t.append(gate_table(srows, f"Table / the {side} side's {want} gates"))
            t.append(detail_note(srows))
            t.append(missing_callout(srows, f"the {side} side"))
            if want == "ade":
                t.append(measures_table(sub, sspec, srecs.get("sim_pvt")))
            if want == "vde":
                t.append(ppa_section(srecs, sspec, "vde"))
            sides[side] = {"released": sok, "missing": [r["gate"] for r in srows if not r["ran"] and not r.get("na")]}
    if skill != "msde":
        t.append(ppa_section(recs, spec, skill))
    t.append(r"\section{Layout}")
    if figs.get("layout"):
        t.append(r"\begin{hsfigure}{Figure 2 / layout}{" + tex(figs["gds"]) + r", rendered with klayout.}" + "\n" +
                 r"\includegraphics[width=\linewidth]{figures/layout.png}" + "\n" + r"\end{hsfigure}")
    else:
        t.append(r"\textcolor{accent}{No layout render: " + tex(figs.get("layout_why", "no GDS in the workspace")) + ".}\n")
    t.append(r"\section{Release record}")
    t.append(rel + "\n")
    t.append(r"\hsprovenance{Built by the chip-flow design-document script from state.json, reports/ and spec/ in the "
             r"block's workspace; state.json last updated " + tex(data.get("updated", "?")) + ".}")
    t.append(r"\end{document}")
    summary = {"released": released,
               "missing": [r["gate"] for r in rows if not r["ran"] and not r.get("na")],
               "failing": [r["gate"] for r in rows if r["ran"] and r["status"] != "pass"],
               "sides": sides}
    return "\n".join(t) + "\n", summary


PREAMBLE = r"""\documentclass[11pt]{article}
\usepackage{housestyle}
\usepackage{amssymb}
\usepackage{tabularx,xltabular,needspace}
\newcolumntype{Y}{>{\raggedright\arraybackslash}X}
\setlength{\LTpre}{0pt}\setlength{\LTpost}{0pt}
\titleformat{\subsection}{\hssubheadfont\fontsize{13pt}{17pt}\selectfont\color{ink}}{}{0pt}{}
\titlespacing*{\subsection}{0pt}{16pt}{4pt}
\renewcommand{\sectionmark}[1]{\markboth{#1}{}}
\hssection{\leftmark}
"""


def builder_dir(env) -> Path:
    d = Path(env.get("PDF_MATERIAL_BUILDER") or
             Path.home() / ".claude" / "skills" / "pdf-material-builder")
    if not (d / "scripts" / "build.sh").is_file():
        raise DocError(f"no pdf-material-builder at {d}",
                       "install the pdf-material-builder skill, or set "
                       "PDF_MATERIAL_BUILDER to its directory")
    return d


def run(argv=None, env=None) -> tuple[dict, str | None, int]:
    env = dict(os.environ if env is None else env)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--file", action="store_true",
                    help="file a released design's document in the register")
    ap.add_argument("--no-build", action="store_true",
                    help="write the .tex and figure specs only")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    ws = Path(args.workspace).resolve()
    data = load_state(ws)
    block = data.get("block", ws.name)
    doc = ws / "doc"
    (doc / "figures").mkdir(parents=True, exist_ok=True)
    spec = load_yaml(ws / "spec" / "spec.yaml") or {}
    (doc / "figures" / "block.json").write_text(
        json.dumps(block_diagram(ws, data, spec), indent=1), encoding="utf-8")
    figs: dict = {"block": True}
    gds = find_gds(ws, data)
    png = doc / "figures" / "layout.png"
    if gds is None:
        figs["layout_why"] = "no GDS in the workspace (the block has not been hardened or laid out)"
    elif args.no_build:
        figs["layout_why"] = "not rendered (--no-build)"
    else:
        why = render_layout(gds, png)
        if why:
            figs["layout_why"] = why
        else:
            figs.update(layout=True, gds=str(gds.relative_to(ws)))
    body, summary = build_tex(ws, data, figs)
    tex_path = doc / f"{block}_design.tex"
    (doc / "preamble.tex").write_text(PREAMBLE, encoding="utf-8")
    tex_path.write_text(body, encoding="utf-8")
    refusal = filing_refusal(ws, data, env) if args.file else "--file not given"
    result = {"script": SCRIPT, "workspace": str(ws), "block": block,
              "skill": data["skill"], "tex": str(tex_path), **summary,
              "filed": False, "not_filed_because": refusal, "register": None}
    if args.no_build:
        result.update(status="pass", pdf=None)
        return result, args.out, 0
    build_env = {k: v for k, v in env.items()
                 if not k.startswith("DOC_")}  # a stray DOC_* must not file a draft
    if refusal is None:
        build_env.update(DOC_PROJECT=env.get("CHIPFLOW_DOC_PROJECT", DEFAULT_PROJECT),
                         DOC_TITLE=f"{block} design document")
    build = builder_dir(env) / "scripts" / "build.sh"
    p = subprocess.run(["bash", str(build), str(tex_path)], env=build_env,
                       capture_output=True, text=True, timeout=900)
    pdf = tex_path.with_suffix(".pdf")
    if p.returncode != 0:
        raise DocError("build.sh failed: " + (p.stderr or p.stdout).strip()[-800:],
                       "read the LaTeX error above; the .tex is at " + str(tex_path))
    reg = [ln for ln in p.stdout.splitlines() if ln.startswith("register:")]
    result.update(status="pass", pdf=str(pdf), filed=bool(reg and refusal is None),
                  register=reg[0][len("register:"):].strip() if reg else None)
    return result, args.out, 0


def main(argv=None) -> int:
    checklib.utf8_stdout()
    try:
        payload, out, code = run(argv)
    except DocError as exc:
        print(json.dumps({"script": SCRIPT, "status": "error", "error": str(exc),
                          "remediation": exc.remediation}))
        return 2
    except Exception as exc:  # noqa: BLE001  (any error -> exit 2)
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}",
                          "remediation": str(exc)}))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
