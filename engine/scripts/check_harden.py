#!/usr/bin/env python
"""check_harden.py - the harden gate (docs/design.md 1.5, "### M4."). Runs
as a job (`jobs.py start --gate harden`, 1.7); this script IS the work a
detached `gate.py` invocation does when it runs.

    check_harden.py --workspace DIR [--out FILE]

Generates `harden/tt_um_<top>.v` and `harden/config.json`/`harden/info.yaml`
from spec.yaml's `tt_pins` (engine/lib/ttlib.py), then runs LibreLane 3
against the vendored TT GF180 template through `bin/eda librelane`
(never a bare `librelane` - docs/design.md 1.2). Passes when the flow
finishes with no failing step and `runs/<tag>/final/` carries GDS, LEF,
netlist, SDF and metrics (gates.yaml's `harden` row).

Per-design config: `harden/config.json` is REGENERATED on every run, so a
hand edit to it is discarded. A design's own LibreLane keys go in
`harden/config.override.json` - a JSON object merged last (e.g.
`{"RUN_POST_GRT_RESIZER_TIMING": 1}` for a small setup miss). It may not
set a key the engine or the TT template owns (tile/PDK keys, VERILOG_FILES,
the template's DO-NOT-CHANGE block, CLOCK_PERIOD - the clock is spec.yaml's):
such a key is a CheckError, exit 2. The file is its own `harden_override`
input in engine/reference/invalidation.yaml, so editing it stales harden.

Restart: the run tag is fixed ("run") and every invocation passes
`--overwrite`. LibreLane's own implicit resume (omit `--overwrite`, let it
load `runs/<tag>`'s latest `state_out.json`) was tried here first and is not
safe on the installed LibreLane 3 build - replayed even against a run
directory an EARLIER, fully successful attempt had left in `final/` with
nothing left to do, it still crashed (FileNotFoundError inside
`Step._reroute_env`, a step directory the flow never created, after
climbing well past the original run's own highest step number instead of
recognizing completion). A job killed halfway therefore finishes by redoing
the whole flow, not by continuing from its last completed step - correct
over clever, and on these tiny corpus designs a full run is a few minutes.

Failure classification (docs/design.md 1.9's "a gate that didn't run is a
refusal, never a pass", enforced here three ways):
  - the launcher never reached LibreLane at all (bad tt_pins so no wrapper
    could be generated, `eda` itself missing/misconfigured, a timeout
    before `runs/<tag>/flow.log` even appears) -> CheckError, exit 2.
  - LibreLane ran and a real step failed (nonzero exit, `flow.log` exists)
    -> a `violations` finding, exit 1 - this is the fault gates.yaml names
    ("a design too large for the tile": global placement/legalization
    fails outright).
  - LibreLane exits 0 but `final/` is missing an expected format -> a
    `violations` finding (a launcher/flow that silently drops output is
    exactly the "missing report" case the review calls out), never a
    silent pass.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
REPO = ENGINE.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import speclib  # noqa: E402
import ttlib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_harden"
EDA_BIN = REPO / "bin" / "eda"
RUN_TAG = "run"
# LibreLane genuinely can run for the better part of an hour on a real
# design (docs/design.md section 1); this default is a ceiling against a
# truly hung tool, not a budget - CHIP_FLOW_HARDEN_TIMEOUT_S overrides it
# (tests use a small value to prove a timeout fails the gate, not a real
# one).
DEFAULT_TIMEOUT_S = 3600.0
EXPECTED_FORMATS = ("gds", "lef", "nl", "sdf", "spef", "metrics.json")


def _pdk_root() -> Path:
    proc = subprocess.run([str(EDA_BIN), "--print-toolchain-root"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError(f"eda --print-toolchain-root failed: "
                         f"{proc.stderr.strip()}")
    return Path(proc.stdout.strip()) / "foss" / "pdks"


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    ap.add_argument("--timeout-s", type=float,
                    default=float(os.environ.get("CHIP_FLOW_HARDEN_TIMEOUT_S",
                                                  DEFAULT_TIMEOUT_S)))
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    spec = speclib.load_spec(ws / "spec" / "spec.yaml")
    top = spec.get("top")
    if not isinstance(top, str) or not top.strip():
        raise CheckError("spec.yaml has no non-empty 'top' - run spec_lint first")
    if not spec.get("tt_pins"):
        raise CheckError("spec.yaml has no 'tt_pins' mapping - harden needs "
                         "one to generate the TT wrapper (engine/lib/ttlib.py)")
    problems = ttlib.validate_tt_pins(spec)
    if problems:
        raise CheckError("tt_pins does not fit the tile: " + "; ".join(problems))

    rtl_dir = ws / "rtl"
    rtl_files = sorted(rtl_dir.glob("*.v")) + sorted(rtl_dir.glob("*.sv"))
    if not rtl_files:
        raise CheckError(f"no .v/.sv files under {rtl_dir}")

    harden_dir = ws / "harden"
    harden_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = harden_dir / f"{ttlib.wrapper_name(spec)}.v"
    try:
        wrapper_path.write_text(ttlib.generate_tt_wrapper(spec), encoding="utf-8")
    except ttlib.TTError as exc:
        raise CheckError(str(exc)) from exc

    # The override is read and refused before config.json is rewritten or
    # the toolchain is asked for: a forbidden key is a CheckError (exit 2,
    # the message its remediation), never a silently dropped edit.
    try:
        override = ttlib.load_harden_override(
            harden_dir / ttlib.HARDEN_OVERRIDE_NAME)
    except ttlib.TTError as exc:
        raise CheckError(str(exc)) from exc
    pdk_root = _pdk_root()
    config = ttlib.harden_config(spec, rtl_files, wrapper_path, pdk_root,
                                 override=override)
    config_path = harden_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    ttlib.write_info_yaml(spec, harden_dir / "info.yaml")

    run_dir = harden_dir / "runs" / RUN_TAG
    resumed = run_dir.is_dir()
    # `--overwrite` always: LibreLane's OWN implicit resume (omit
    # --overwrite, let it pick up runs/<tag>'s latest state_out.json) was
    # tried here first and is not safe on this LibreLane build - re-running
    # it against a run directory left by an EARLIER, fully successful run
    # (nothing left to do) still crashed (FileNotFoundError inside
    # Step._reroute_env, a step directory it never created, after climbing
    # past the original run's own highest step number instead of recognizing
    # completion). A killed-halfway job therefore resumes by redoing the
    # whole flow, not by continuing from its last completed step; on these
    # tiny corpus designs a full run is a few minutes, and correctness (never
    # feeding a half-written run directory back into a fresh flow object)
    # matters more than the minutes saved. `resumed` is still recorded so a
    # caller can tell a restart from a first run.
    tech = ttlib.gf180_tech()
    cmd = [str(EDA_BIN), "librelane", "--pdk-root", str(pdk_root),
          *tech.librelane_pdk_args.split(), "--run-tag", RUN_TAG,
          "--overwrite", str(config_path)]

    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=str(harden_dir), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=args.timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(
            f"eda librelane timed out after {args.timeout_s:g}s: {exc}") from exc
    wall_s = round(time.monotonic() - t0, 2)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")

    flow_log = run_dir / "flow.log"
    if not flow_log.is_file():
        # never reached LibreLane's own flow object at all - a launcher
        # failure (bad args, missing binary, a config that failed to even
        # load), not a real flow attempt: refuse, never record a violation
        # that looks like an evaluated design.
        raise CheckError(
            f"librelane never started (no {flow_log}, exit {proc.returncode}): "
            f"{output[-2000:]}")

    violations = []
    if proc.returncode != 0:
        error_log = run_dir / "error.log"
        detail = (error_log.read_text(encoding="utf-8", errors="replace")
                  if error_log.is_file() else output[-2000:])
        violations.append(checklib.violation(
            "harden", "error", None, top, "flow_step_failed", [],
            f"librelane exited {proc.returncode}: {detail[-2000:]}",
            "librelane", resumed=resumed, wall_s=wall_s))
    else:
        final_dir = run_dir / "final"
        missing = [fmt for fmt in EXPECTED_FORMATS
                  if not any((final_dir / fmt).glob("*"))
                  and not (final_dir / fmt).is_file()]
        if not final_dir.is_dir():
            missing = list(EXPECTED_FORMATS)
        if missing:
            violations.append(checklib.violation(
                "harden", "error", None, top, "harden_missing_artifact",
                [], f"librelane exited 0 but {final_dir} is missing: "
                f"{missing}", "check_harden", resumed=resumed, wall_s=wall_s))

    metrics = {}
    metrics_path = run_dir / "final" / "metrics.json"
    if metrics_path.is_file():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metrics = {}
    metric_subset = {k: v for k, v in metrics.items()
                     if k.startswith(("design__instance", "design__die",
                                      "design__core", "timing__setup",
                                      "timing__hold"))
                     and ":" not in k}

    payload = checklib.report(SCRIPT, ws / "rtl", violations, top=top,
                              run_tag=RUN_TAG, resumed=resumed, wall_s=wall_s,
                              run_dir=str(run_dir), metrics=metric_subset)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
