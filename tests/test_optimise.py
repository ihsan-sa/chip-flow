"""engine/scripts/optimise.py: the sizing optimise loop, `start` + `numeric`
(docs/design.md section 4, "### M8."). Fast tests fake `eda` with a simple,
deterministic function of the sizing parameter it was handed (y = x), so
scipy's real differential_evolution runs against a trivial but REAL
objective with no actual ngspice call. test_real_r2r_dac_optimise_reaches_
bounds_from_wrong_start (slow) runs the whole thing against the REAL
corpus/ade/r2r_dac rung and the real gf180mcuD toolchain - this milestone's
own done criterion.

The RTL loop (M7: `start`/`trial`/`finish`) is tested below the M8 tests:
fast ones over real git and hashing with the tools faked, and one slow
test on the real corpus UART with the real tools."""
from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
CORPUS_R2R = REPO / "corpus" / "ade" / "r2r_dac"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import optimise  # noqa: E402
import sim_run  # noqa: E402
from checklib import CheckError  # noqa: E402

import pytest  # noqa: E402

SPEC_YAML = "top: widget\nsupply: {vdd: 3.3}\ndevices: [xm1]\nrequirements: []\n"

BENCH_TEMPLATE = """\
.include '{{PDK}}/libs.tech/ngspice/design.spice'
.lib '{{PDK}}/libs.tech/ngspice/sm141064.spice' {{CORNER}}
.temp {{TEMP_C}}
.include '{{NETLIST}}'
{{SIZING}}
.control
op
print v(vdd)
.endc
.end
"""


def make_ws(tmp_path: Path, start_value: float = -3.0) -> Path:
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb", "sizing"):
        (ws / sub).mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(SPEC_YAML, encoding="utf-8")
    (ws / "netlist" / "widget.cir").write_text("* netlist\n", encoding="utf-8")
    (ws / "tb" / "widget_tb.cir").write_text(BENCH_TEMPLATE, encoding="utf-8")
    (ws / "tb" / "widget_tb.bounds.json").write_text(
        json.dumps([{"measure": "y", "min": 0.9, "max": 1.1}]),
        encoding="utf-8")
    import yaml
    (ws / "sizing" / "sizing.yaml").write_text(yaml.safe_dump({
        "x": {"value": start_value, "min": -5.0, "max": 5.0}}), encoding="utf-8")
    return ws


def make_identity_fake_eda(tmp_path: Path) -> Path:
    """Reports measure y = the deck's own `.param x=...` value - a trivial,
    real (not mocked out) function scipy's differential_evolution optimizes
    against, with no ngspice call."""
    fake_root = tmp_path / "fake_toolchain"
    (fake_root / "foss" / "pdks" / "gf180mcuD").mkdir(parents=True)
    script = tmp_path / "fake_eda"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --print-toolchain-root ]; then\n"
        f"  echo '{fake_root}'\n  exit 0\nfi\n"
        "deck=\"$3\"\n"
        "x=$(grep -oE '\\.param x=[-0-9.eE+]+' \"$deck\" | cut -d= -f2)\n"
        "echo\n"
        "echo '  Measurements for Transient Analysis'\n"
        "echo \"y                     =  $x\"\n",
        encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_start_freezes_evaluator_and_writes_empty_tsv(tmp_path):
    ws = make_ws(tmp_path)
    payload, _ = optimise.run_start(
        ["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    assert payload["status"] == "pass"
    meta = json.loads((ws / optimise.META_PATH).read_text())
    assert meta["target"] == "sizing/sizing.yaml"
    tsv_lines = (ws / optimise.TSV_PATH).read_text().splitlines()
    assert tsv_lines == ["\t".join(optimise.TSV_FIELDS)]


def test_numeric_without_start_raises(tmp_path, monkeypatch):
    ws = make_ws(tmp_path)
    eda = make_identity_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    with pytest.raises(CheckError):
        optimise.run_numeric(["--workspace", str(ws)])


def test_numeric_detects_evaluator_drift(tmp_path, monkeypatch):
    ws = make_ws(tmp_path)
    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    # a bench edit after `start` must be caught, not silently trusted.
    (ws / "tb" / "widget_tb.cir").write_text(BENCH_TEMPLATE + "* edited\n",
                                             encoding="utf-8")
    eda = make_identity_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    with pytest.raises(CheckError):
        optimise.run_numeric(["--workspace", str(ws)])


def test_numeric_reaches_bounds_from_a_wrong_start(tmp_path, monkeypatch):
    ws = make_ws(tmp_path, start_value=-3.0)  # deliberately wrong: -3 is
    # nowhere near [0.9, 1.1], the bound y=x must land inside.
    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    eda = make_identity_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    payload, _ = optimise.run_numeric(
        ["--workspace", str(ws), "--popsize", "8", "--maxiter", "20", "--seed", "1"])
    assert payload["status"] == "pass", payload
    assert payload["improved"] is True
    assert payload["best_score"] >= 0
    import yaml
    final = yaml.safe_load((ws / "sizing" / "sizing.yaml").read_text())
    assert 0.9 <= final["x"]["value"] <= 1.1

    tsv_lines = (ws / optimise.TSV_PATH).read_text().splitlines()
    assert len(tsv_lines) > 2  # header + start row + at least one DE trial
    assert tsv_lines[1].split("\t")[-1] == "starting sizing"

    # design.md 4: "the winner run[s] the full corner set" - the identity
    # fake eda is corner-invariant (y = x regardless of which .lib section
    # the deck selects), so the winner holds at every corner too, and that
    # is now checked and reported, not left silently unexercised.
    assert payload["full_corner_pass"] is True
    assert set(payload["full_corner_corners"]) == {"tt", "ss", "ff", "sf", "fs"}
    assert payload["full_corner_violations"] == []
    assert tsv_lines[-1].split("\t")[-1] == "winner, full corner set"


def test_numeric_leaves_file_untouched_when_nothing_improves(tmp_path, monkeypatch):
    # start already at the optimum (y = x = 1.0, comfortably inside
    # [0.9, 1.1]) - the search must not report an "improvement" that never
    # happened, and must never rewrite a file it did not actually beat.
    ws = make_ws(tmp_path, start_value=1.0)
    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    eda = make_identity_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)
    payload, _ = optimise.run_numeric(
        ["--workspace", str(ws), "--popsize", "6", "--maxiter", "10", "--seed", "1"])
    assert payload["status"] == "pass", payload
    import yaml
    final = yaml.safe_load((ws / "sizing" / "sizing.yaml").read_text())
    assert final["x"]["value"] == 1.0


def test_tsv_rows_survive_a_crash_mid_search(tmp_path, monkeypatch):
    # trials.tsv used to be buffered in memory and written once, all at
    # the end, AFTER differential_evolution returned - a crash partway
    # through a real (minutes-long, hundreds-of-ngspice-calls) search lost
    # every trial that had already run. Each row must be flushed to disk
    # as its own trial completes, not batched.
    ws = make_ws(tmp_path)
    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    eda = make_identity_fake_eda(tmp_path)
    monkeypatch.setattr(sim_run, "EDA_BIN", eda)

    calls = {"n": 0}

    def crashing_de(objective, bounds, **kwargs):
        for _ in range(3):
            objective([calls["n"]])  # each call appends its own tsv row
            calls["n"] += 1
        raise RuntimeError("simulated crash mid-search")

    import scipy.optimize
    monkeypatch.setattr(scipy.optimize, "differential_evolution", crashing_de)

    with pytest.raises(RuntimeError):
        optimise.run_numeric(["--workspace", str(ws)])

    # despite the crash, every trial that ran before it (the start row +
    # the 3 objective() calls) is already on disk.
    tsv_lines = (ws / optimise.TSV_PATH).read_text().splitlines()
    assert len(tsv_lines) == 1 + 1 + 3, tsv_lines  # header + start + 3 trials


def test_winner_runs_full_corner_set_and_reports_a_corner_only_failure(tmp_path, monkeypatch):
    # design.md 4: "the winner run[s] the full corner set" - a winner that
    # only holds at 'tt' (every trial's own, cheap, tt-only evaluator) must
    # still be caught and reported once the search concludes, even though
    # `status` itself stays keyed on the tt-only evaluator (the DAC's own
    # documented done-criterion, unlike the mirror's).
    ws = make_ws(tmp_path, start_value=1.0)  # already "optimal" at tt
    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])

    fake_root = tmp_path / "fake_toolchain"
    (fake_root / "foss" / "pdks" / "gf180mcuD").mkdir(parents=True)
    script = tmp_path / "corner_sensitive_fake_eda"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --print-toolchain-root ]; then\n"
        f"  echo '{fake_root}'\n  exit 0\nfi\n"
        "deck=\"$3\"\n"
        "x=$(grep -oE '\\.param x=[-0-9.eE+]+' \"$deck\" | cut -d= -f2)\n"
        # at 'ss' the fake reports a value well outside [0.9, 1.1]
        # regardless of x - every other corner (including tt) reports x
        # unchanged, same as make_identity_fake_eda.
        "if grep -q \"sm141064.spice' ss\" \"$deck\"; then y=0.2; else y=\"$x\"; fi\n"
        "echo\n"
        "echo '  Measurements for Transient Analysis'\n"
        "echo \"y                     =  $y\"\n",
        encoding="utf-8")
    import stat as stat_mod
    script.chmod(script.stat().st_mode | stat_mod.S_IEXEC)
    monkeypatch.setattr(sim_run, "EDA_BIN", script)

    payload, _ = optimise.run_numeric(
        ["--workspace", str(ws), "--popsize", "6", "--maxiter", "10", "--seed", "1"])

    assert payload["status"] == "pass", payload  # tt-only evaluator: unaffected
    assert payload["full_corner_pass"] is False
    assert payload["full_corner_violations"] != []
    # checklib.violation() sorts refs alphabetically, so don't assume an
    # index - just confirm 'ss' (and only 'ss') shows up across every ref.
    corner_names = {"tt", "ss", "ff", "sf", "fs"}
    bad_corners = {r for v in payload["full_corner_violations"]
                  for r in v["refs"] if r in corner_names}
    assert bad_corners == {"ss"}
    tsv_lines = (ws / optimise.TSV_PATH).read_text().splitlines()
    last = tsv_lines[-1].split("\t")
    assert last[-1] == "winner, full corner set"
    assert last[-2] == "False"  # the 'kept' column doubles as pass/fail here


@pytest.mark.slow
def test_real_r2r_dac_optimise_reaches_bounds_from_wrong_start(tmp_path):
    """This milestone's own done criterion, run for real: corpus/ade/r2r_dac
    ships with a deliberately wrong 1:1 R:2R ratio (netlist/r2r_dac.cir's
    own comment explains why sim_tt is not in that rung's faults/
    manifest.yaml) - `optimise.py numeric` against its real sizing.yaml and
    the real gf180mcuD toolchain must reach every measure inside bounds."""
    ws = tmp_path / "ws"
    for sub in ("spec", "netlist", "tb", "sizing"):
        (ws / sub).mkdir(parents=True)
    shutil.copy2(CORPUS_R2R / "spec.yaml", ws / "spec" / "spec.yaml")
    shutil.copy2(CORPUS_R2R / "netlist" / "r2r_dac.cir",
                ws / "netlist" / "r2r_dac.cir")
    for f in (CORPUS_R2R / "tb").iterdir():
        shutil.copy2(f, ws / "tb" / f.name)
    shutil.copy2(CORPUS_R2R / "sizing" / "sizing.yaml",
                ws / "sizing" / "sizing.yaml")

    import yaml
    start = yaml.safe_load((ws / "sizing" / "sizing.yaml").read_text())
    assert start["r_length"]["value"] == start["r2_length"]["value"], (
        "the shipped corpus rung is supposed to start wrong (1:1) - if this "
        "ever fires, the corpus file itself changed under this test")

    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    payload, _ = optimise.run_numeric(
        ["--workspace", str(ws), "--popsize", "8", "--maxiter", "20", "--seed", "1"])

    assert payload["status"] == "pass", payload
    assert payload["improved"] is True
    assert payload["best_score"] >= 0

    final = yaml.safe_load((ws / "sizing" / "sizing.yaml").read_text())
    ratio = final["r2_length"]["value"] / final["r_length"]["value"]
    assert ratio == pytest.approx(2.0, rel=0.05)

    # and the sim_tt gate itself, which failed on the untouched rung, now
    # passes against the optimiser's own winning sizing.
    sys.path.insert(0, str(SCRIPTS))
    import check_sim_tt
    code = check_sim_tt.main(["--workspace", str(ws)])
    assert code == 0


# ------------------------------------------------------------ RTL loop (M7)
#
# Fast tests: real git, real hashing, the real diff/revert and must_keep
# check; only the tools are faked. run_constraints fails `sim` when the RTL
# says SIMFAIL; run_metric's area is the RTL's line count and its netlist
# holds one cell per `cellname u_name (` instance line, so deleting an
# instance really does drop it from what must_keep_missing reads.

import re  # noqa: E402
import subprocess  # noqa: E402

RTL_SPEC = """\
top: blk
requirements:
  - {id: REQ-A, text: a, check: sim}
ports:
  clk: {dir: input, width: 1}
  q: {dir: output, width: 1}
clock: {period_ns: 20, domains: [clk]}
must_keep: [u_ring]
"""
RTL_BASE = """\
module blk (input wire clk, output reg q);
  keeper u_ring (.a(q));
  // pad 1
  // pad 2
  // pad 3
  always @(posedge clk) q <= ~q;
endmodule
"""


def git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), *args], check=True,
                          capture_output=True, text=True).stdout


def make_rtl_ws(tmp_path: Path, monkeypatch) -> Path:
    ws = tmp_path / "ws"
    for sub in ("spec", "rtl", "tb", "formal", "holdout"):
        (ws / sub).mkdir(parents=True)
    (ws / "spec" / "spec.yaml").write_text(RTL_SPEC, encoding="utf-8")
    (ws / "rtl" / "blk.v").write_text(RTL_BASE, encoding="utf-8")
    (ws / "tb" / "test_blk.py").write_text("# req: REQ-A\n", encoding="utf-8")
    (ws / "formal" / "blk_formal.sv").write_text("// f\n", encoding="utf-8")
    (ws / "holdout" / "test_h.py").write_text("# h\n", encoding="utf-8")
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "-c", "user.name=t", "-c", "user.email=t@l", "commit", "-qm", "init")

    lib_root = tmp_path / "tc"
    lib = lib_root / optimise_check_synth().LIBERTY_REL
    lib.parent.mkdir(parents=True)
    lib.write_text("library (fake) {}\n", encoding="utf-8")
    monkeypatch.setattr(optimise_check_synth(), "toolchain_root",
                        lambda timeout=30.0: lib_root)

    def fake_constraints(ew, detail):
        text = (ew / "rtl" / "blk.v").read_text(encoding="utf-8")
        sim = "fail" if "SIMFAIL" in text else "pass"
        if sim == "fail":
            detail.append("check_sim: test_blk failed")
        return {"lint": "pass", "sim": sim,
                "formal": "pass" if sim == "pass" else "skipped"}

    def fake_metric(ew, ev, profile, with_power):
        text = (ew / "rtl" / "blk.v").read_text(encoding="utf-8")
        cells = {m: {} for m in re.findall(r"^\s*\w+ (u_\w+) \(", text, re.M)}
        nl = {"modules": {"blk": {"cells": cells, "netnames": {}, "ports": {
            "clk": {"direction": "input", "bits": [2]},
            "q": {"direction": "output", "bits": [3]}}}}}
        keep = json.loads((ev / "must_keep.json").read_text(encoding="utf-8"))
        missing = optimise.must_keep_missing(nl, "blk", keep)
        ports = json.loads((ev / "ports.json").read_text(encoding="utf-8"))
        return {"area": float(len(text.splitlines())), "slack": 5.0,
                "power": 1e-3, "synth": "pass",
                "ports": "fail" if optimise.port_mismatch(nl, "blk", ports)
                else "pass",
                "must_keep": "fail" if missing else "pass",
                "detail": [f"must_keep {n} removed" for n in missing]}

    monkeypatch.setattr(optimise, "run_constraints", fake_constraints)
    monkeypatch.setattr(optimise, "run_metric", fake_metric)
    return ws


def optimise_check_synth():
    import check_synth
    return check_synth


def rtl_start(ws: Path, **kw) -> dict:
    argv = ["--workspace", str(ws), "--target", "rtl/blk.v"]
    for k, v in kw.items():
        argv += [f"--{k}", str(v)]
    payload, _ = optimise.run_rtl_start(argv)
    return payload


def rtl_trial(ws: Path, note: str = "t") -> dict:
    payload, _ = optimise.run_trial(["--workspace", str(ws), "--note", note])
    return payload


def tsv_rows(ws: Path) -> list[dict]:
    import csv
    with open(ws / "optimise" / "trials.tsv", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def edit_target(ws: Path, old: str, new: str) -> None:
    p = ws / "rtl" / "blk.v"
    text = p.read_text(encoding="utf-8")
    assert old in text
    p.write_text(text.replace(old, new), encoding="utf-8")


def test_rtl_start_freezes_evaluator_and_scores_the_baseline(tmp_path, monkeypatch):
    ws = make_rtl_ws(tmp_path, monkeypatch)
    payload = rtl_start(ws)
    ev = ws / "optimise" / "evaluator"
    for name in ("synth.ys", "design.sdc", "liberty.json", "ports.json",
                 "must_keep.json", "holdout.json", "spec.yaml",
                 "tb/test_blk.py", "formal/blk_formal.sv"):
        assert (ev / name).is_file(), name
    assert "create_clock -name clk -period 20" in (ev / "design.sdc").read_text()
    meta = payload["meta"]
    assert meta["evaluator_sha"] == optimise.hash_rtl_evaluator(ws)
    assert meta["baseline"]["area"] == 7.0
    rows = tsv_rows(ws)
    assert list(rows[0]) == optimise.RTL_TSV_FIELDS
    assert rows[0]["trial"] == "0" and rows[0]["kept"] == "True"


def test_rtl_start_refuses_a_dirty_tree(tmp_path, monkeypatch):
    ws = make_rtl_ws(tmp_path, monkeypatch)
    (ws / "tb" / "test_blk.py").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(CheckError, match="uncommitted changes"):
        rtl_start(ws)


def test_rtl_kept_trial_is_committed_and_a_worse_one_reverted(tmp_path, monkeypatch):
    ws = make_rtl_ws(tmp_path, monkeypatch)
    rtl_start(ws)
    edit_target(ws, "  // pad 1\n", "")
    kept = rtl_trial(ws, "drop a pad line")
    assert kept["kept"] is True and kept["area"] == 6.0
    assert "optimise trial 1: drop a pad line" in git(ws, "log", "-1", "--format=%s")
    edit_target(ws, "  // pad 2\n", "  // pad 2\n  // more\n  // more\n")
    worse = rtl_trial(ws, "grow it")
    assert worse["kept"] is False
    assert "// more" not in (ws / "rtl" / "blk.v").read_text()
    rows = tsv_rows(ws)
    assert [r["kept"] for r in rows] == ["True", "True", "False"]


def test_rtl_evaluator_edit_mid_loop_aborts_and_reverts(tmp_path, monkeypatch):
    """M7 done-criterion: an evaluator edit mid-loop aborts and reverts."""
    ws = make_rtl_ws(tmp_path, monkeypatch)
    rtl_start(ws)
    edit_target(ws, "  // pad 1\n", "")
    assert rtl_trial(ws)["kept"] is True
    kept_text = (ws / "rtl" / "blk.v").read_text()

    edit_target(ws, "  // pad 2\n", "")
    sdc = ws / "optimise" / "evaluator" / "design.sdc"
    sdc.write_text(sdc.read_text().replace("-period 20", "-period 200"))
    with pytest.raises(CheckError, match="evaluator changed"):
        rtl_trial(ws, "loosen the clock")
    assert (ws / "rtl" / "blk.v").read_text() == kept_text
    rows = tsv_rows(ws)
    assert rows[-1]["kept"] == "False" and "ABORTED" in rows[-1]["note"]
    with pytest.raises(CheckError, match="aborted"):
        rtl_trial(ws)


def test_rtl_edit_outside_target_is_reverted(tmp_path, monkeypatch):
    """M7 done-criterion: an edit outside the target is reverted."""
    ws = make_rtl_ws(tmp_path, monkeypatch)
    lock = ws / "state.json.lock"  # state.py's own lock: engine output
    lock.write_text("", encoding="utf-8")
    git(ws, "add", "state.json.lock")
    git(ws, "-c", "user.name=t", "-c", "user.email=t@l", "commit", "-qm", "lock")
    rtl_start(ws)
    lock.write_text("held\n", encoding="utf-8")
    tb = ws / "tb" / "test_blk.py"
    tb.write_text("# req: REQ-A\nassert True  # weakened\n", encoding="utf-8")
    (ws / "spec" / "extra.yaml").write_text("x: 1\n", encoding="utf-8")
    (ws / "holdout" / "test_h.py").unlink()
    edit_target(ws, "  // pad 1\n", "")
    payload = rtl_trial(ws, "shrink, and touch the tests")
    assert sorted(payload["reverted_outside_target"]) == [
        "holdout/test_h.py", "spec/extra.yaml", "tb/test_blk.py"]
    assert tb.read_text() == "# req: REQ-A\n"
    assert lock.read_text() == "held\n"
    assert not (ws / "spec" / "extra.yaml").exists()
    assert (ws / "holdout" / "test_h.py").read_text() == "# h\n"
    # the target's own change was still scored, and kept
    assert payload["kept"] is True
    assert "tb/test_blk.py" not in git(ws, "show", "--stat", "HEAD")
    assert "reverted outside target" in tsv_rows(ws)[-1]["note"]


def test_rtl_trial_failing_sim_is_logged_not_kept(tmp_path, monkeypatch):
    """M7 done-criterion: a trial failing `sim` is logged as not kept -
    even though it is smaller."""
    ws = make_rtl_ws(tmp_path, monkeypatch)
    rtl_start(ws)
    edit_target(ws, "  // pad 1\n  // pad 2\n", "  // SIMFAIL\n")
    payload = rtl_trial(ws, "smaller but wrong")
    assert payload["kept"] is False
    assert payload["constraints"]["sim"] == "fail"
    row = tsv_rows(ws)[-1]
    assert row["sim"] == "fail" and row["kept"] == "False"
    assert row["area"] == "" and "rejected (sim)" in row["note"]
    assert "SIMFAIL" not in (ws / "rtl" / "blk.v").read_text()


def test_rtl_trial_removing_must_keep_cell_is_rejected(tmp_path, monkeypatch):
    """M7 done-criterion: a trial removing a `must_keep` cell is rejected,
    even though it is smaller and passes sim."""
    ws = make_rtl_ws(tmp_path, monkeypatch)
    rtl_start(ws)
    edit_target(ws, "  keeper u_ring (.a(q));\n", "")
    payload = rtl_trial(ws, "the ring does nothing the tests see")
    assert payload["kept"] is False
    assert payload["constraints"]["sim"] == "pass"
    assert payload["constraints"]["must_keep"] == "fail"
    assert payload["area"] < 7.0  # smaller, and still rejected
    row = tsv_rows(ws)[-1]
    assert row["must_keep"] == "fail" and row["kept"] == "False"
    assert "u_ring" in (ws / "rtl" / "blk.v").read_text()


def test_must_keep_missing_reads_flattened_names():
    nl = {"modules": {"top": {"cells": {"u_ring.inv0": {}, "$abc$1": {}},
                              "netnames": {"\\sync_q": {}}}}}
    assert optimise.must_keep_missing(nl, "top", ["u_ring", "sync_q"]) == []
    assert optimise.must_keep_missing(nl, "top", ["inv0"]) == []
    assert optimise.must_keep_missing(nl, "top", ["u_gone"]) == ["u_gone"]


def test_port_mismatch_catches_a_dropped_or_narrowed_port():
    ports = {"a": {"dir": "input", "width": 8}, "y": {"dir": "output"}}
    nl = {"modules": {"t": {"ports": {
        "a": {"direction": "input", "bits": list(range(4))}}}}}
    diffs = optimise.port_mismatch(nl, "t", ports)
    assert any(d.startswith("a:") for d in diffs)
    assert any(d.startswith("y:") for d in diffs)


def test_rtl_loop_stops_on_patience_and_trials(tmp_path, monkeypatch):
    ws = make_rtl_ws(tmp_path, monkeypatch)
    rtl_start(ws, trials=10, patience=2)
    rtl_trial(ws, "no change")
    payload = rtl_trial(ws, "no change again")
    assert payload["stop"] and payload["stop"].startswith("patience")
    with pytest.raises(CheckError, match="stopped"):
        rtl_trial(ws)


def test_rtl_head_moved_outside_the_loop_aborts(tmp_path, monkeypatch):
    ws = make_rtl_ws(tmp_path, monkeypatch)
    rtl_start(ws)
    (ws / "tb" / "test_blk.py").write_text("# sneaky\n", encoding="utf-8")
    git(ws, "-c", "user.name=t", "-c", "user.email=t@l", "commit", "-qam", "x")
    with pytest.raises(CheckError, match="HEAD"):
        rtl_trial(ws)


def test_rtl_finish_discards_a_winner_failing_full_gates(tmp_path, monkeypatch):
    """section 4: a winner that fails holdout/formal/mutate is discarded
    and the last passing trial restored."""
    ws = make_rtl_ws(tmp_path, monkeypatch)
    rtl_start(ws)
    edit_target(ws, "  // pad 1\n", "")
    assert rtl_trial(ws, "first")["kept"]
    first = (ws / "rtl" / "blk.v").read_text()
    edit_target(ws, "  // pad 2\n", "  // DROPS_HOLDOUT\n")
    edit_target(ws, "  // pad 3\n", "")
    assert rtl_trial(ws, "second")["kept"]

    def fake_full(ws_, detail):
        bad = "DROPS_HOLDOUT" in (ws_ / "rtl" / "blk.v").read_text()
        if bad:
            detail.append("check_holdout: a held-out test failed")
        return {"holdout": "fail" if bad else "pass", "formal": "pass",
                "mutate": "pass"}

    monkeypatch.setattr(optimise, "full_gates", fake_full)
    payload, _ = optimise.run_finish(["--workspace", str(ws)])
    assert payload["status"] == "pass"
    assert payload["winner"]["trial"] == 1 and payload["discarded"] == [2]
    assert payload["tried"][0]["detail"] == ["check_holdout: a held-out test failed"]
    assert (ws / "rtl" / "blk.v").read_text() == first
    assert "restore trial 1" in git(ws, "log", "-1", "--format=%s")
    notes = [r["note"] for r in tsv_rows(ws)[-2:]]
    assert "holdout=fail" in notes[0] and "holdout=pass" in notes[1]


CORPUS_UART = REPO / "corpus" / "vde" / "uart"
# Drop the frame counter: the frame ends when only the stop bit is left in
# `shift`. Smaller, same frames - the kept area trial.
DROP_BITS_LEFT = [
    ("  localparam FRAME_BITS = 4'd11; // start + 8 data + parity + stop\n", ""),
    ("  reg [3:0]  bits_left;\n", ""),
    ("      bits_left <= 4'd0;\n", ""),
    ("        bits_left <= FRAME_BITS;\n", ""),
    ("        bits_left <= bits_left - 4'd1;\n", ""),
    ("        if (bits_left == 4'd1)\n", "        if (shift[10:1] == 10'd0)\n"),
]


@pytest.mark.slow
def test_real_uart_loop_keeps_area_win_rejects_sim_fail_and_winner_passes(tmp_path):
    """M7 on the real corpus UART and the real tools: a trial that breaks
    the start bit fails `sim` and is not kept, a trial that drops the frame
    counter is kept with less area and slack >= 0, and `finish` runs
    holdout, unbounded formal and mutate on that winner, which passes."""
    sys.path.insert(0, str(ENGINE / "lib"))
    import faults
    ws = faults.make_scratch_workspace(tmp_path, CORPUS_UART, "vde", "uart")
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "-c", "user.name=t", "-c", "user.email=t@l", "commit", "-qm", "init")
    target = ws / "rtl" / "uart_tx.v"

    start, _ = optimise.run_rtl_start(["--workspace", str(ws), "--target",
                                       "rtl/uart_tx.v", "--trials", "5"])
    base = start["baseline"]
    assert base["passed"] and base["slack"] >= 0, base

    text = target.read_text(encoding="utf-8")
    bad = "shift     <= {1'b1, ^data, data, 1'b0};"
    assert bad in text
    target.write_text(text.replace(bad, bad.replace("1'b0}", "1'b1}")),
                      encoding="utf-8")
    t1 = rtl_trial(ws, "start bit driven high")
    assert t1["kept"] is False and t1["constraints"]["sim"] == "fail", t1
    assert target.read_text(encoding="utf-8") == text

    for old, new in DROP_BITS_LEFT:
        assert old in text, old
        text = text.replace(old, new)
    target.write_text(text, encoding="utf-8")
    t2 = rtl_trial(ws, "drop bits_left; end on shift[10:1] == 0")
    assert t2["kept"] is True, t2
    assert t2["area"] < base["area"] and t2["slack"] >= 0, t2

    done, _ = optimise.run_finish(["--workspace", str(ws)])
    assert done["status"] == "pass", done
    assert done["winner"]["trial"] == 2 and done["discarded"] == []
    assert done["tried"][0]["gates"] == {"holdout": "pass", "formal": "pass",
                                         "mutate": "pass"}
    assert "bits_left" not in target.read_text(encoding="utf-8")
    rows = tsv_rows(ws)
    assert [r["kept"] for r in rows[:3]] == ["True", "False", "True"]


def test_sizing_start_and_numeric_record_state_optimise(tmp_path, monkeypatch):
    # design.md 4: sizing "runs the same loop", and the loop records its
    # evaluator hash in state.optimise - a session resuming from state.json
    # alone must see that a sizing search ran, not optimise: null.
    import state as state_mod
    ws = make_ws(tmp_path, start_value=-3.0)
    state_mod.State.init(ws, "ade", "widget")
    optimise.run_start(["--workspace", str(ws), "--target", "sizing/sizing.yaml"])
    meta = json.loads((ws / optimise.META_PATH).read_text())
    rec = json.loads((ws / "state.json").read_text())["optimise"]
    assert rec == {"trials": 0, "evaluator_sha": meta["evaluator_sha"],
                   "best": {"trial": None, "score": None}}
    monkeypatch.setattr(sim_run, "EDA_BIN", make_identity_fake_eda(tmp_path))
    payload, _ = optimise.run_numeric(
        ["--workspace", str(ws), "--popsize", "6", "--maxiter", "5", "--seed", "1"])
    rec = json.loads((ws / "state.json").read_text())["optimise"]
    assert rec["trials"] == payload["trials"]
    assert rec["evaluator_sha"] == meta["evaluator_sha"]
    assert rec["best"]["score"] == pytest.approx(payload["best_score"], abs=1e-6)
    assert rec["best"]["trial"] > 0  # -3 is out of bounds; DE beat it
