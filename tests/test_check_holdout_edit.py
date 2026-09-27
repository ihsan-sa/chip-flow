"""engine/scripts/check_holdout_edit.py: a fix for a held-out stimulus
fault may move stimulus and helper calls, never what the tests judge.
Hermetic (pure AST), no simulator."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "scripts"))
sys.path.insert(0, str(REPO / "engine" / "lib"))

import check_holdout_edit  # noqa: E402

# the shape of the /msde PLL track's own held-out file before its fix
BASE = """\
import cocotb
from cocotb.triggers import Timer
import vis as v

REF_T = 100.0


def decode_n(code):
    return code + 1


async def ref_clock(dut, rises):
    for t in rises:
        await Timer(t, unit="ns")


# req: REQ-LOCK
@cocotb.test()
async def test_lock(dut):
    fb, rises = await v.pfd_setup(dut, [-10.0] * 60)
    await Timer(1000, unit="ns")
    assert int(dut.lock.value) == 1, "lock not high"
    assert all(x <= 0.5 for _, x in v.pulses(fb)), "other side pulsed"


# req: REQ-DIV
@cocotb.test()
async def test_nsel(dut):
    for code in (5, 0, 4, 7):
        n = decode_n(code)
        want = 8
        await Timer(int(6 * 8 * n * v.TV + 400), unit="ns")
        r = v.rises(dut)
        assert len(r) >= 2, "stalled"
        v.check_period("n", r, want * n * v.TV)
"""

# the accepted fix: a helper now returns three values, a settle wait grew,
# a priming edge was added - stimulus only
STIMULUS_FIX = (BASE
                .replace("fb, rises = await", "fb, rises, _ = await")
                .replace("int(6 * 8 * n * v.TV + 400)",
                         "int(settle + 6 * 8 * n * v.TV + 400)")
                .replace("        n = decode_n(code)\n",
                         "        n = decode_n(code)\n"
                         "        settle = 2 * 8 * max(n, 5) * v.TV\n"))

LOOSENINGS = {
    "assert_bound": (">= 2", ">= 1", "holdout_judgement_changed"),
    "expected_in_call": ("want * n * v.TV)", "want * n * v.TV * 1.1)",
                         "holdout_judgement_changed"),
    "expected_moved_out": ("want = 8", "want = 9", "holdout_bound_changed"),
    "fewer_cases": ("(5, 0, 4, 7)", "(5, 0)", "holdout_judgement_changed"),
    "guarded_off": ('    assert int(dut.lock.value) == 1, "lock not high"',
                    '    if False:\n'
                    '        assert int(dut.lock.value) == 1, "lock not high"',
                    "holdout_judgement_changed"),
    "early_return": ("    fb, rises, _ = await",
                     "    return\n    fb, rises, _ = await",
                     "holdout_control_changed"),
    "swallowed": ('    assert int(dut.lock.value) == 1, "lock not high"',
                  '    try:\n'
                  '        assert int(dut.lock.value) == 1, "lock not high"\n'
                  '    except AssertionError:\n'
                  '        pass', "holdout_control_changed"),
    "skipped": ("# req: REQ-LOCK\n@cocotb.test()",
                "# req: REQ-LOCK\n@cocotb.test(skip=True)",
                "holdout_tests_changed"),
    "req_dropped": ("# req: REQ-DIV\n", "# req: REQ-LOCK\n",
                    "holdout_req_changed"),
    "model_changed": ("return code + 1", "return code + 2",
                      "holdout_model_changed"),
    "constant_changed": ("REF_T = 100.0", "REF_T = 90.0",
                         "holdout_bound_changed"),
    "observed_faked": ("r = v.rises(dut)", "r = [0, want * n * v.TV]",
                       "holdout_bound_changed"),
    "input_frozen": ("n = decode_n(code)\n", "n = decode_n(0)\n",
                     "holdout_bound_changed"),
    "observed_overridden": ("        r = v.rises(dut)\n",
                            "        r = v.rises(dut)\n"
                            "        r = [0, want * n * v.TV]\n",
                            "holdout_bound_changed"),
}


def _dirs(tmp_path: Path, cur_text: str) -> tuple[Path, Path]:
    base, cur = tmp_path / "base", tmp_path / "cur"
    base.mkdir()
    cur.mkdir()
    (base / "test_h.py").write_text(BASE, encoding="utf-8")
    (cur / "test_h.py").write_text(cur_text, encoding="utf-8")
    return base, cur


def test_unchanged_and_stimulus_only_edits_pass(tmp_path):
    base, cur = _dirs(tmp_path, BASE)
    assert check_holdout_edit.compare(base, cur) == []
    (cur / "test_h.py").write_text(STIMULUS_FIX, encoding="utf-8")
    assert STIMULUS_FIX != BASE
    assert check_holdout_edit.compare(base, cur) == []


@pytest.mark.parametrize("name", sorted(LOOSENINGS))
def test_an_edit_that_loosens_what_is_judged_is_refused(tmp_path, name):
    old, new, kind = LOOSENINGS[name]
    assert old in STIMULUS_FIX, name
    # the loosening rides on top of an otherwise-acceptable stimulus fix
    base, cur = _dirs(tmp_path, STIMULUS_FIX.replace(old, new, 1))
    kinds = {v["kind"] for v in check_holdout_edit.compare(base, cur)}
    assert kind in kinds, (name, kinds)


def test_removed_file_is_refused(tmp_path):
    base, cur = _dirs(tmp_path, BASE)
    (base / "test_more.py").write_text(BASE, encoding="utf-8")
    kinds = {v["kind"] for v in check_holdout_edit.compare(base, cur)}
    assert "holdout_file_removed" in kinds


def _ws(tmp_path: Path, cur_text: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "holdout").mkdir(parents=True)
    (ws / "holdout" / "test_h.py").write_text(cur_text, encoding="utf-8")
    snap = ws / "state_snapshots" / "pre-fix-holdout-a1" / "holdout"
    snap.mkdir(parents=True)
    (snap / "test_h.py").write_text(BASE, encoding="utf-8")
    return ws


def test_cli_contract(tmp_path, capsys):
    ws = _ws(tmp_path, STIMULUS_FIX)
    code = check_holdout_edit.main(["--workspace", str(ws), "--baseline",
                                    "pre-fix-holdout-a1"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["status"] == "pass", out
    (ws / "holdout" / "test_h.py").write_text(
        STIMULUS_FIX.replace(">= 2", ">= 1"), encoding="utf-8")
    code = check_holdout_edit.main(["--workspace", str(ws), "--baseline",
                                    "pre-fix-holdout-a1"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1, out
    # a missing baseline is a refusal, never a pass
    code = check_holdout_edit.main(["--workspace", str(ws), "--baseline",
                                    "no-such-label"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["status"] == "error", out
    code = check_holdout_edit.main(["--workspace", str(ws), "--baseline",
                                    "../x"])
    assert code == 2
    capsys.readouterr()
