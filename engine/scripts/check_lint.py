#!/usr/bin/env python
"""check_lint.py - the lint gate (docs/design.md 1.5, "### M2.").

    check_lint.py --workspace DIR [--out FILE]

Runs `verilator --lint-only -Wall -Wno-fatal --top-module <spec.yaml top>`
(bin/eda's own loader-wrapping dispatch, docs/design.md 1.2 - launched as a
subprocess of `bin/eda`, the same path tests/check.sh's own verilator-lint
smoke uses, never a bare host `verilator`) over every rtl/*.v and rtl/*.sv
file. `-Wno-fatal` matters here: without it verilator stops and exits
nonzero the moment ANY warning fires, which would collapse "no errors,
warnings only from an allowlist" into one bit; with it every warning is
printed and this script does its own severity classification against the
allowlist, using verilator's own exit code only to catch a real compile
error (a bad literal `%Error:` line with no rule, never allowlist-able).

Pass criteria (gates.yaml `lint` row): no errors; warnings only from an
allowlist with reasons. The allowlist is `rtl/lint_allow.yaml` (optional; a
list of `{rule, reason}`) - it lives inside rtl/ on purpose, not as a
separate artifact kind, so the existing `rtl` dir_text hash already covers
an allowlist edit (docs/design.md 1.6). A warning whose rule is not on the
list is reported at severity "error" (gates.yaml's fail_severities is
`[error]` for every M1-registered row, so an unlisted warning must be
elevated to fail, not merely surfaced at "warning"); an allowlisted warning
is downgraded to "info" (visible in the report, never counted). Fault this
gate must catch: "an always @* missing an else (latch)" - verilator's own
LATCH rule, live under plain -Wall, with no reason to allowlist it.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import speclib  # noqa: E402
from checklib import CheckError  # noqa: E402

import yaml  # noqa: E402

SCRIPT = "check_lint"
EDA_BIN = REPO / "bin" / "eda"
ALLOW_REL = "rtl/lint_allow.yaml"
TIMEOUT_S = 60.0

# %Warning-RULE: file:line:col: msg   (a lint rule warning)
# %Error: file:line:col: msg          (a real compile error, never allowlist-able)
# %Error: Exiting due to N error(s)   (verilator's own summary - not a finding)
LINE_RE = re.compile(
    r"^%(?P<sev>Error|Warning)(?:-(?P<rule>[A-Z0-9_]+))?:\s*"
    r"(?:(?P<file>\S+?):(?P<line>\d+):(?P<col>\d+):\s*)?(?P<msg>.*)$")


def load_allowlist(ws: Path) -> dict[str, str]:
    p = ws / ALLOW_REL
    if not p.is_file():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise CheckError(f"{p} must be a YAML list of {{rule, reason}}")
    allow: dict[str, str] = {}
    for i, entry in enumerate(data):
        if not isinstance(entry, dict) or not entry.get("rule") \
                or not entry.get("reason"):
            raise CheckError(f"{p}[{i}] must have a non-empty 'rule' and "
                             "'reason'")
        allow[str(entry["rule"])] = str(entry["reason"])
    return allow


def collect_sources(ws: Path) -> list[Path]:
    rtl_dir = ws / "rtl"
    files = sorted(rtl_dir.glob("*.v")) + sorted(rtl_dir.glob("*.sv"))
    if not files:
        raise CheckError(f"no .v/.sv files under {rtl_dir}")
    return files


def run_verilator(ws: Path, top: str, files: list[Path]) -> str:
    rel = [str(f.relative_to(ws)) for f in files]
    try:
        proc = subprocess.run(
            [str(EDA_BIN), "verilator", "--lint-only", "-Wall", "-Wno-fatal",
             "--top-module", top, *rel],
            cwd=str(ws), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"verilator --lint-only timed out after "
                         f"{TIMEOUT_S:g}s: {exc}") from exc
    return (proc.stdout or "") + (proc.stderr or "")


def parse_violations(output: str, allow: dict[str, str]) -> list[dict]:
    violations = []
    for line in output.splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        msg = m.group("msg").strip()
        if msg.startswith("Exiting due to"):
            continue  # verilator's own summary line, not a finding
        sev, rule = m.group("sev"), m.group("rule")
        file_ = m.group("file")
        line_no = int(m.group("line")) if m.group("line") else None
        if sev == "Error":
            violations.append(checklib.violation(
                "lint", "error", file_, None, rule or "compile_error", [],
                msg, "verilator", line=line_no))
            continue
        # Warning: allowlisted -> info (visible, never counted); otherwise
        # elevated to error (fail_severities: [error] is what gate.py counts).
        reason = allow.get(rule) if rule else None
        sev_out = "info" if reason is not None else "error"
        extras = {"reason": reason} if reason is not None else {}
        violations.append(checklib.violation(
            "lint", sev_out, file_, None, rule or "warning", [], msg,
            "verilator", line=line_no, **extras))
    return violations


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = spec.get("top")
    if not isinstance(top, str) or not top.strip():
        raise CheckError("spec.yaml has no non-empty 'top' - run spec_lint "
                         "first")
    files = collect_sources(ws)
    allow = load_allowlist(ws)
    output = run_verilator(ws, top, files)
    violations = parse_violations(output, allow)
    payload = checklib.report(SCRIPT, ws, violations, top=top,
                              files=[str(f.relative_to(ws)) for f in files])
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
