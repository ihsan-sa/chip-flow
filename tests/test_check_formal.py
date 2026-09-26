"""engine/scripts/check_formal.py: the formal gate (docs/design.md 1.5's
`formal` row, "### M3."). Runs REAL SymbiYosys (smtbmc yices + abc pdr)
through the eda image - not hermetic, needs the eda image, same as
test_check_lint.py's own verilator-lint smoke."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ENGINE / "lib"))

import check_formal  # noqa: E402
import gate  # noqa: E402
import yaml as _yaml  # noqa: E402

GATES_YAML = ENGINE / "reference" / "gates.yaml"


def _formal_gate_row():
    rows = _yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]["vde"]
    return rows["formal"]


RTL_OK = """\
module top (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= rst ? 4'd0 : count + 4'd1;
endmodule
"""

# gates.yaml's own named fault for `formal`: "a register with no reset: sim
# passes, formal fails".
RTL_NO_RESET = """\
module top (input wire clk, input wire rst, output reg [3:0] count);
  always @(posedge clk) count <= count + 4'd1;
endmodule
"""

FORMAL_SV = """\
module top_formal (input wire clk, input wire rst, output wire [3:0] count);
  top dut (.clk(clk), .rst(rst), .count(count));
`ifdef FORMAL
  reg past_valid = 0;
  always @(posedge clk) past_valid <= 1;
  always @(posedge clk)
    if (past_valid)
      REQ_RESET: assert (!$past(rst) || count == 4'd0);
  always @(posedge clk)
    COVER_MAX: cover (count == 4'hF);
`endif
endmodule
"""

SPEC = """\
top: top
requirements:
  - id: REQ-RESET
    text: reset proven for all time, not just what a sim test tries
    check: formal
    property: REQ_RESET
formal:
  depth: 12
"""


def make_ws(tmp_path: Path, rtl_text: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "rtl").mkdir(parents=True)
    (ws / "spec").mkdir(parents=True)
    (ws / "formal").mkdir(parents=True)
    (ws / "rtl" / "top.v").write_text(rtl_text, encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(SPEC, encoding="utf-8")
    (ws / "formal" / "top_formal.sv").write_text(FORMAL_SV, encoding="utf-8")
    return ws


@pytest.mark.slow
def test_clean_design_proves_with_both_engines(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_OK)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["proven"] == ["REQ-RESET"]
    assert out["smt_status"] == "PASS"
    assert out["pdr_status"] == "PASS"
    assert out["cover_points"] == ["COVER_MAX"]
    assert out["bounded"] == [] and out["failed"] == []


@pytest.mark.slow
def test_register_with_no_reset_fails(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_NO_RESET)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    kinds = {v["kind"] for v in out["violations"]}
    assert "property_failed" in kinds
    assert out["failed"] == ["REQ-RESET"]
    assert out["smt_status"] == "FAIL"
    gate_result = gate.evaluate("formal", _formal_gate_row(), out)
    assert gate_result["status"] == "fail"


SPEC_PROPERTY_IS_A_COVER = """\
top: top
requirements:
  - id: REQ-RESET
    text: reset proven for all time, not just what a sim test tries
    check: formal
    property: COVER_MAX
formal:
  depth: 12
"""


@pytest.mark.slow
def test_property_naming_a_cover_point_is_refused(tmp_path, capsys):
    # a `property:` label must be the ASSERT it claims to be - COVER_MAX is
    # a real label in formal/*.sv's own model (FORMAL_SV's `cover`), so
    # `label not in smt_cases` would not catch this; without the type/
    # skipped check this sails through classify_property and comes back
    # "proven" with nothing actually proven about it.
    ws = make_ws(tmp_path, RTL_OK)
    (ws / "spec" / "spec.yaml").write_text(SPEC_PROPERTY_IS_A_COVER,
                                           encoding="utf-8")
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "COVER_MAX" in out["remediation"]


def test_sby_done_error_is_refused_for_any_task(tmp_path, capsys, monkeypatch):
    # DONE (ERROR) still matches DONE_RE (run_sby's own guard is only for a
    # launcher that never reaches DONE at all) - an ERRORed task must be
    # refused too, not silently folded into bounded/proven downstream.
    ws = make_ws(tmp_path, RTL_OK)
    fake_eda = tmp_path / "fake-eda.sh"
    fake_eda.write_text("#!/bin/sh\necho 'DONE (ERROR)'\nexit 0\n",
                        encoding="utf-8")
    fake_eda.chmod(0o755)
    monkeypatch.setattr(check_formal, "EDA_BIN", fake_eda)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "DONE (ERROR)" in out["remediation"]


def test_no_formal_requirement_is_an_error(tmp_path, capsys):
    # an empty property set is a refusal, never a vacuous pass.
    ws = make_ws(tmp_path, RTL_OK)
    (ws / "spec" / "spec.yaml").write_text(
        "top: top\nrequirements:\n  - id: REQ-X\n    text: x\n    check: sim\n",
        encoding="utf-8")
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert out["status"] == "error"
    assert "empty property set" in out["remediation"]


@pytest.mark.slow
def test_missing_property_label_is_an_error(tmp_path, capsys):
    # a 'property' spec.yaml names that formal/*.sv never defines (a typo,
    # or a `bind` that silently failed to attach - see check_formal.py's own
    # header) is refused, never silently skipped.
    ws = make_ws(tmp_path, RTL_OK)
    (ws / "spec" / "spec.yaml").write_text(
        "top: top\nrequirements:\n"
        "  - id: REQ-RESET\n    text: x\n    check: formal\n"
        "    property: NOT_A_REAL_LABEL\nformal:\n  depth: 12\n",
        encoding="utf-8")
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "NOT_A_REAL_LABEL" in out["remediation"]


def test_no_rtl_files_is_an_error(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_OK)
    for f in (ws / "rtl").glob("*.v"):
        f.unlink()
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no *" in out["remediation"]


def test_crashed_launcher_is_an_error(tmp_path, capsys, monkeypatch):
    # a failed launcher (nonzero exit, no DONE line) fails the gate - never
    # silently treated as "no findings".
    ws = make_ws(tmp_path, RTL_OK)
    bad_eda = tmp_path / "bad-eda.sh"
    bad_eda.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
    bad_eda.chmod(0o755)
    monkeypatch.setattr(check_formal, "EDA_BIN", bad_eda)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "no DONE line" in out["remediation"]


def test_task_substatus_parses_basecase_and_induction():
    text = ("engine_0 (smtbmc yices) returned pass for basecase\n"
           "engine_0 (smtbmc yices) returned FAIL for induction\n")
    assert check_formal.task_substatus(text) == {
        "basecase": "pass", "induction": "FAIL"}
    assert check_formal.task_substatus("nothing here at all") == {
        "basecase": None, "induction": None}


CASE_OK = {"failed": False}
CASE_FAILED = {"failed": True}


def test_classify_property_proven():
    verdict, violation = check_formal.classify_property(
        "P", "REQ", CASE_OK, {"basecase": "pass", "induction": "pass"},
        "PASS", 20)
    assert verdict == "proven" and violation is None


def test_classify_property_failed_by_smtbmc():
    verdict, violation = check_formal.classify_property(
        "P", "REQ", CASE_FAILED, {"basecase": "FAIL", "induction": "FAIL"},
        "FAIL", 20)
    assert verdict == "failed"
    assert violation["kind"] == "property_failed"
    assert violation["severity"] == "error"


def test_classify_property_induction_trace_is_not_a_counterexample():
    # The uart run: basecase passed to depth 20, the induction step failed
    # on p_start_ignored_while_busy (sby tags that property <failure> with
    # trace_induct.vcd) and abc pdr proved it. An unreachable induction
    # start state is bounded, not a counterexample.
    verdict, violation = check_formal.classify_property(
        "P", "REQ", CASE_FAILED, {"basecase": "pass", "induction": "FAIL"},
        "PASS", 20)
    assert verdict == "bounded"
    assert violation["kind"] == "bounded_not_proven"
    # The planted fault: the same <failure> with the basecase failed is a
    # real counterexample and still fails the gate.
    verdict, violation = check_formal.classify_property(
        "P", "REQ", CASE_FAILED, {"basecase": "FAIL", "induction": None},
        "PASS", 20)
    assert verdict == "failed"
    assert violation["kind"] == "property_failed"


def test_classify_property_engine_disagreement():
    # smtbmc claims proven, but abc pdr found a counterexample it did not -
    # never trusted silently.
    verdict, violation = check_formal.classify_property(
        "P", "REQ", CASE_OK, {"basecase": "pass", "induction": "pass"},
        "FAIL", 20)
    assert verdict == "failed"
    assert violation["kind"] == "engine_disagreement"


def test_classify_property_bounded_when_induction_does_not_converge(capsys):
    # docs/design.md section 2: "A property that only reaches a bounded
    # depth is recorded as bounded with its depth, and the gate never
    # reports it as proven."
    verdict, violation = check_formal.classify_property(
        "P", "REQ", CASE_OK, {"basecase": "pass", "induction": None},
        "PASS", 20)
    assert verdict == "bounded"
    assert violation["kind"] == "bounded_not_proven"
    assert violation["severity"] == "info"
    assert violation["depth"] == 20
    gate_result = gate.evaluate(
        "formal", _formal_gate_row(),
        {"violations": [violation], "counts": {"total": 1}})
    assert gate_result["status"] == "pass"  # info severity never fails the gate


def test_classify_property_pdr_error_raises():
    # pdr's own DONE reached ERROR (a solver crash) - never silently
    # dropped through to "bounded" just because smtbmc's basecase passed.
    import pytest
    from checklib import CheckError
    with pytest.raises(CheckError):
        check_formal.classify_property(
            "P", "REQ", CASE_OK, {"basecase": "pass", "induction": "pass"},
            "ERROR", 20)


def test_classify_property_pdr_unknown_raises():
    import pytest
    from checklib import CheckError
    with pytest.raises(CheckError):
        check_formal.classify_property(
            "P", "REQ", CASE_OK, {"basecase": "pass", "induction": None},
            "UNKNOWN", 20)


def test_wrong_kind_labels_ignores_skipped_asserts():
    # M5, docs/design.md-cited: a skipped ASSERT is still an ASSERT - it
    # falls back to classify_property's task-level basecase/induction
    # verdict, and must never be refused as a label/kind mismatch just
    # because sby marked it <skipped/> when SOME OTHER property in the same
    # run needed more induction depth.
    props = {"P_OK": "REQ-OK", "P_SKIPPED": "REQ-SKIPPED"}
    smt_cases = {
        "P_OK": {"type": "ASSERT", "failed": False, "skipped": False},
        "P_SKIPPED": {"type": "ASSERT", "failed": False, "skipped": True},
    }
    assert check_formal.wrong_kind_labels(props, smt_cases) == []


def test_wrong_kind_labels_catches_a_cover_point_named_as_an_assert():
    props = {"COVER_MAX": "REQ-X"}
    smt_cases = {"COVER_MAX": {"type": "COVER", "failed": False,
                               "skipped": False}}
    assert check_formal.wrong_kind_labels(props, smt_cases) == ["COVER_MAX"]


def test_classify_property_no_verdict_at_all_is_an_error():
    import pytest
    from checklib import CheckError
    with pytest.raises(CheckError):
        check_formal.classify_property(
            "P", "REQ", CASE_OK, {"basecase": None, "induction": None},
            "PASS", 20)


@pytest.mark.slow
def test_relative_workspace_proves_cleanly(tmp_path, capsys, monkeypatch):
    """M5 regression (found running the real /vde skill end to end on
    counter8): run_sby's own `cwd=str(sby_dir)` combined with an
    unresolved-relative `config`/`workdir` (both built from the same,
    possibly-relative, --workspace) doubled the workspace's own relative
    prefix under sby's cwd and it could never find its own config file.
    A relative --workspace must still prove cleanly."""
    ws = make_ws(tmp_path, RTL_OK)
    monkeypatch.chdir(tmp_path)
    code = check_formal.main(["--workspace", "ws"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["status"] == "pass"
    assert out["proven"] == ["REQ-RESET"]


# A DUT with an internal register the wrapper can only reach by bind or by
# hierarchical reference: q counts 0..9 and wraps, so `q <= 9` is an
# invariant and `q <= 8` is false.
RTL_DECADE = """\
module top (input wire clk, input wire rst, output wire [3:0] count);
  reg [3:0] q = 4'd0;
  always @(posedge clk) q <= (rst || q == 4'd9) ? 4'd0 : q + 4'd1;
  assign count = q;
endmodule
"""

# The regression the spi_fifo /vde run found: a bound checker whose assert
# is always false. yosys's native frontend drops the bind, so the assert
# never reached sby's model; it must come back as a failure.
FORMAL_BIND_FALSE = """\
module chk (input wire clk, input wire [3:0] q);
  B_FALSE: assert property (@(posedge clk) 1'b0);
endmodule
bind top chk u_chk (.clk(clk), .q(q));
module top_formal (input wire clk, input wire rst, output wire [3:0] count);
  top dut (.clk(clk), .rst(rst), .count(count));
endmodule
"""

FORMAL_HIER = """\
module top_formal (input wire clk, input wire rst, output wire [3:0] count);
  top dut (.clk(clk), .rst(rst), .count(count));
  always @(posedge clk) begin : props
    H_INV: assert (dut.q <= 4'd%d);
  end
endmodule
"""


def _spec_for(label: str) -> str:
    return ("top: top\nrequirements:\n"
            "  - id: REQ-P\n    text: x\n    check: formal\n"
            f"    property: {label}\nformal:\n  depth: 12\n")


def _ws_with(tmp_path: Path, rtl: str, formal: str, label: str) -> Path:
    ws = make_ws(tmp_path, rtl)
    (ws / "formal" / "top_formal.sv").write_text(formal, encoding="utf-8")
    (ws / "spec" / "spec.yaml").write_text(_spec_for(label), encoding="utf-8")
    return ws


@pytest.mark.slow
def test_bound_always_false_assert_fails(tmp_path, capsys):
    ws = _ws_with(tmp_path, RTL_DECADE, FORMAL_BIND_FALSE, "B_FALSE")
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["frontend"] == "slang"
    assert out["failed"] == ["REQ-P"] and out["proven"] == []


@pytest.mark.slow
def test_hierarchical_ref_invariant_proves_and_false_one_fails(tmp_path, capsys):
    # natively `dut.q` is an undriven wire (a free input), or - inside a
    # named block - the assert is dropped outright; either way nothing
    # about the DUT would be proven.
    ws = _ws_with(tmp_path / "t", RTL_DECADE, FORMAL_HIER % 9, "H_INV")
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["frontend"] == "slang" and out["frontend_why"]
    assert out["proven"] == ["REQ-P"]

    ws = _ws_with(tmp_path / "f", RTL_DECADE, FORMAL_HIER % 8, "H_INV")
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    assert out["failed"] == ["REQ-P"]


@pytest.mark.slow
def test_plain_wrapper_stays_on_the_native_frontend(tmp_path, capsys):
    ws = make_ws(tmp_path, RTL_OK)
    check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert out["frontend"] == "native" and out["frontend_why"] is None


def test_bind_without_slang_is_refused(tmp_path, capsys, monkeypatch):
    ws = _ws_with(tmp_path, RTL_DECADE, FORMAL_BIND_FALSE, "B_FALSE")
    monkeypatch.setattr(check_formal, "probe", lambda *a: "")
    monkeypatch.setattr(check_formal, "slang_plugin", lambda: None)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "bind" in out["remediation"] and "yosys-slang" in out["remediation"]


def test_slang_that_cannot_read_the_design_is_refused(tmp_path, capsys,
                                                       monkeypatch):
    ws = _ws_with(tmp_path, RTL_DECADE, FORMAL_HIER % 9, "H_INV")
    native = "Warning: Identifier `\\dut.q' is implicitly declared.\n"
    monkeypatch.setattr(check_formal, "probe",
                        lambda fe, *a: native if fe == "native" else "ERROR: x")
    monkeypatch.setattr(check_formal, "slang_plugin", lambda: tmp_path)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "dut.q" in out["remediation"] and "could not read" in out["remediation"]


def test_hier_refs_reads_only_dotted_implicit_identifiers():
    log = ("x.sv:4: Warning: Identifier `\\dut.q' is implicitly declared.\n"
           "x.sv:5: Warning: Identifier `\\plain' is implicitly declared.\n")
    assert check_formal.hier_refs(log) == ["dut.q"]
    assert check_formal.hier_refs("no warnings here") == []


def test_has_bind_ignores_comments(tmp_path):
    commented = tmp_path / "a.sv"
    commented.write_text("// never `bind`:\n/* bind top chk u (); */\n"
                         "module m; endmodule\n", encoding="utf-8")
    bound = tmp_path / "b.sv"
    bound.write_text("bind top chk u_chk (.clk(clk));\n", encoding="utf-8")
    assert not check_formal.has_bind([commented])
    assert check_formal.has_bind([commented, bound])


def test_count_properties_none_when_probe_never_got_there():
    top = "top_formal"
    assert check_formal.count_properties("ERROR: parse", top) is None
    log = f"{check_formal.PROBE_MARK}\ntop_formal/$5\ntop_formal/\\L\n"
    assert check_formal.count_properties(log, top) == 2


def test_resolve_labels_matches_leaf_and_refuses_ambiguity():
    cases = {"A": {}, "dut.u_chk.B": {}, "blk.C": {}, "x.C": {}}
    props = {"A": "R1", "B": "R2", "C": "R3", "D": "R4"}
    ids, ambiguous = check_formal.resolve_labels(props, cases)
    assert ids == {"A": "A", "B": "dut.u_chk.B"}
    assert ambiguous == ["C"]


# ------------------------------------------------ depth from the spec only
# A depth nobody chose once let a cover needing ~1100 cycles come back "not
# reached to depth 20" beside asserts "bounded to depth 20". The depth is
# the design's to set (spec.yaml formal.depth); the gate never defaults it.

SPEC_NO_DEPTH = SPEC.replace("formal:\n  depth: 12\n", "")


def _spy_eda(tmp_path: Path) -> tuple[Path, Path]:
    marker = tmp_path / "eda-called"
    eda = tmp_path / "spy-eda.sh"
    eda.write_text(f"#!/bin/sh\ntouch {marker}\necho 'DONE (PASS)'\nexit 0\n",
                   encoding="utf-8")
    eda.chmod(0o755)
    return eda, marker


def test_missing_formal_depth_is_refused_before_any_tool_runs(
        tmp_path, capsys, monkeypatch):
    ws = make_ws(tmp_path, RTL_OK)
    (ws / "spec" / "spec.yaml").write_text(SPEC_NO_DEPTH, encoding="utf-8")
    eda, marker = _spy_eda(tmp_path)
    monkeypatch.setattr(check_formal, "EDA_BIN", eda)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2, out
    assert "formal.depth" in out["remediation"]
    assert "at least the cycle length" in out["remediation"]
    assert not marker.exists(), "sby/yosys ran on a depth nobody chose"


@pytest.mark.parametrize("formal, needle", [
    ({}, "no formal.depth"),
    ({"depth": None}, "no formal.depth"),
    ({"depth": 0}, "not a positive integer"),
    ({"depth": -5}, "not a positive integer"),
    ({"depth": "20"}, "not a positive integer"),
    ({"depth": True}, "not a positive integer"),
    ({"depth": 12.5}, "not a positive integer"),
    ({"depth": 20, "cover_depth": 10}, "never shallower"),
    ({"depth": 20, "cover_depth": 0}, "not a positive integer"),
    ({"depth": 20, "timeout_s": 0}, "above 0"),
    ({"depth": 20, "timeout_s": 1e9}, "at most"),
    ({"depth": 20, "timeout_s": "long"}, "above 0"),
])
def test_formal_settings_refuses_bad_or_missing_depth(formal, needle):
    from checklib import CheckError
    with pytest.raises(CheckError, match=needle):
        check_formal.formal_settings({"formal": formal})


def test_formal_settings_refuses_a_non_mapping_formal_key():
    from checklib import CheckError
    with pytest.raises(CheckError, match="not a mapping"):
        check_formal.formal_settings({"formal": 20})
    with pytest.raises(CheckError, match="no formal.depth"):
        check_formal.formal_settings({})


def test_formal_settings_uses_the_spec_depth_and_scales_the_timeout():
    s = check_formal.formal_settings({"formal": {"depth": 12}})
    assert s["depth"] == s["cover_depth"] == 12
    assert s["prove_timeout_s"] == s["cover_timeout_s"] == (
        check_formal.TIMEOUT_BASE_S + check_formal.TIMEOUT_PER_STEP_S * 12)
    deep = check_formal.formal_settings(
        {"formal": {"depth": 40, "cover_depth": 1200}})
    assert (deep["depth"], deep["cover_depth"]) == (40, 1200)
    assert deep["cover_timeout_s"] > deep["prove_timeout_s"] > 180.0
    huge = check_formal.formal_settings({"formal": {"depth": 10 ** 7}})
    assert huge["prove_timeout_s"] == check_formal.TIMEOUT_MAX_S
    fixed = check_formal.formal_settings(
        {"formal": {"depth": 40, "cover_depth": 1200, "timeout_s": 900}})
    assert fixed["prove_timeout_s"] == fixed["cover_timeout_s"] == 900.0


def _fake_sby_run(monkeypatch, cov_failed: bool):
    """Stub out every tool call so run() is exercised end to end without
    sby: records each task's (depth, timeout) and hands back one ASSERT
    that proves and one COVER that is (or is not) reached."""
    seen: dict[str, tuple[int, float]] = {}
    depths: dict[str, int] = {}
    monkeypatch.setattr(check_formal, "pick_frontend",
                        lambda *a: ("native", "stub", None))

    def write_sby(path, sv, rtl, top, mode, engine, depth, *a):
        depths[Path(path).stem] = depth
    monkeypatch.setattr(check_formal, "write_sby", write_sby)

    def run_sby(sby_dir, config, name, timeout_s):
        seen[name] = (depths[name], timeout_s)
        return ("returned pass for basecase\nreturned pass for induction\n"
                "DONE (PASS)\n"), Path(sby_dir) / name
    monkeypatch.setattr(check_formal, "run_sby", run_sby)

    def parse_testcases(xml_path):
        name = Path(xml_path).stem
        return {"REQ_RESET": {"type": "ASSERT", "failed": False,
                              "skipped": name == "cov"},
                "COVER_MAX": {"type": "COVER",
                              "failed": name == "cov" and cov_failed,
                              "skipped": name != "cov"}}
    monkeypatch.setattr(check_formal, "parse_testcases", parse_testcases)
    return seen


def test_explicit_depth_reaches_every_sby_task(tmp_path, capsys, monkeypatch):
    ws = make_ws(tmp_path, RTL_OK)
    (ws / "spec" / "spec.yaml").write_text(
        SPEC.replace("depth: 12\n", "depth: 12\n  cover_depth: 300\n"),
        encoding="utf-8")
    seen = _fake_sby_run(monkeypatch, cov_failed=False)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert (out["depth"], out["cover_depth"]) == (12, 300)
    assert seen["smt"][0] == seen["pdr"][0] == 12
    assert seen["cov"][0] == 300
    assert seen["cov"][1] > seen["smt"][1]


def test_unreached_cover_names_the_depth_and_the_key_to_raise(
        tmp_path, capsys, monkeypatch):
    ws = make_ws(tmp_path, RTL_OK)
    _fake_sby_run(monkeypatch, cov_failed=True)
    code = check_formal.main(["--workspace", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    v = [v for v in out["violations"] if v["kind"] == "cover_not_reached"]
    assert len(v) == 1, out
    assert v[0]["severity"] == "error"
    assert "depth 12 (spec.yaml formal.depth)" in v[0]["msg"]
    # The key to raise is always cover_depth: raising the prove depth to a
    # long cover's reach makes smtbmc's k-induction infeasible.
    assert "raise formal.cover_depth" in v[0]["msg"]
    assert "raise formal.depth" not in v[0]["msg"]
    assert v[0]["depth"] == 12
