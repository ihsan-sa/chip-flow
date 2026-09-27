#!/usr/bin/env python
"""check_holdout_edit.py - is a held-out edit stimulus-only?

    check_holdout_edit.py --workspace DIR --baseline LABEL [--out FILE]

A `holdout_stimulus_fault` (check_holdout.py) is a held-out test that died
in its own stimulus code - a visible tb helper changed shape under it, say -
before any assert judged the design. Its fix edits holdout/, which would
otherwise let a fixer quietly loosen the check the designer never sees. This
compares holdout/*.py against the pre-fix snapshot
`state_snapshots/<LABEL>/holdout/` (state.py snapshot) and passes only when
the edit left every judgement alone: the fixer may adapt stimulus and helper
calls, never asserts, bounds or expected values.

Refused (each a finding, exit 1):
  holdout_file_removed      a baseline holdout/*.py file is gone
  holdout_tests_changed     a cocotb test was added, removed or renamed, or
                            its decorator (skip, expect_fail, timeout) moved
  holdout_req_changed       a test's `# req:` ids changed
  holdout_judgement_changed an assert, a raise, or a call named assert*/
                            check*/expect*/verify* changed - its text, or the
                            if/for/while/with headers that enclose it (so
                            `if False:` around an assert, or fewer loop cases,
                            counts); multiset per function
  holdout_bound_changed     a constant (literal-only) value bound to a name a
                            judgement reads, or a module-level constant,
                            changed - an expected value moved out of the
                            assert is still an expected value
  holdout_control_changed   a return/break/continue/try-except was added to
                            or removed from a function that judges
  holdout_model_changed     a plain (non-async) helper function defined in
                            holdout/ changed - a reference model computes
                            expected values
Allowed: anything else - await/Timer arguments, stimulus sequences, how a
helper's return is unpacked, a new local, a new async driver.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
import cocotblib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_holdout_edit"
SNAP_DIR = "state_snapshots"
JUDGE_CALL_RE = re.compile(r"^_*(assert|check|expect|verify)", re.I)
LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _callee(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _is_const(node: ast.AST) -> bool:
    """Literal-only: a number/string, or a tuple/list/unary/binop of them."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_const(e) for e in node.elts)
    if isinstance(node, ast.UnaryOp):
        return _is_const(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_const(node.left) and _is_const(node.right)
    return False


def _is_test(fn: ast.AST) -> bool:
    return any("cocotb.test" in ast.unparse(d) or ast.unparse(d) == "test"
               for d in fn.decorator_list)


class _Walk(ast.NodeVisitor):
    """Judgement nodes of one function, each keyed by its enclosing
    if/for/while/with headers."""

    def __init__(self):
        self.guards: list[str] = []
        self.judged: Counter = Counter()
        self.reads: set[str] = set()
        self.control: Counter = Counter()

    def _judge(self, node: ast.AST) -> None:
        self.judged[" | ".join(self.guards + [ast.unparse(node)])] += 1
        self.reads |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

    def _guarded(self, header: str, node: ast.AST) -> None:
        self.guards.append(header)
        for child in node.body:
            self.visit(child)
        self.guards.pop()
        for child in getattr(node, "orelse", []):
            self.guards.append("else of " + header)
            self.visit(child)
            self.guards.pop()

    def visit_If(self, node):
        self._guarded("if " + ast.unparse(node.test), node)

    def visit_While(self, node):
        self._guarded("while " + ast.unparse(node.test), node)

    def visit_For(self, node):
        self._guarded(f"for {ast.unparse(node.target)} in "
                      f"{ast.unparse(node.iter)}", node)

    visit_AsyncFor = visit_For

    def visit_With(self, node):
        self._guarded("with " + ", ".join(ast.unparse(i)
                                          for i in node.items), node)

    visit_AsyncWith = visit_With

    def visit_Try(self, node):
        for h in node.handlers:
            self.control["except " + (ast.unparse(h.type) if h.type
                                      else "<bare>")] += 1
        self.generic_visit(node)

    visit_TryStar = visit_Try

    def visit_Assert(self, node):
        self._judge(node)

    def visit_Raise(self, node):
        self._judge(node)

    def visit_Return(self, node):
        self.control["return"] += 1
        self.generic_visit(node)

    def visit_Break(self, node):
        self.control["break"] += 1

    def visit_Continue(self, node):
        self.control["continue"] += 1

    def visit_Call(self, node):
        if JUDGE_CALL_RE.match(_callee(node)):
            self._judge(node)
            return
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        pass  # nested functions are their own scope; summarised separately

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef


def _const_binds(fn: ast.AST, names: set[str]) -> Counter:
    out: Counter = Counter()
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            value = node.value
            if value is None or not _is_const(value):
                continue
            for t in targets:
                hit = {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
                if hit & names:
                    out[ast.unparse(node)] += 1
    return out


def summarise(py: Path) -> dict:
    """Everything this gate compares, for one holdout/*.py file."""
    text = py.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(py))
    except SyntaxError as exc:
        raise CheckError(f"{py.name} does not parse: {exc}") from exc
    funcs: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        w = _Walk()
        for stmt in node.body:
            w.visit(stmt)
        funcs[node.name] = {
            "test": _is_test(node),
            "decorators": [ast.unparse(d) for d in node.decorator_list],
            "judged": w.judged,
            "control": w.control if w.judged else Counter(),
            "binds": _const_binds(node, w.reads),
            "model": (ast.dump(node)
                      if isinstance(node, ast.FunctionDef) else None),
        }
    consts = Counter(ast.unparse(n) for n in tree.body
                     if isinstance(n, (ast.Assign, ast.AnnAssign))
                     and n.value is not None and _is_const(n.value))
    return {"funcs": funcs, "consts": consts}


def compare(base_dir: Path, cur_dir: Path) -> list[dict]:
    """Findings for cur_dir's holdout/*.py against base_dir's."""
    found: list[dict] = []

    def bad(kind: str, msg: str, file: str, module: str | None = None):
        found.append(checklib.violation("holdout_edit", "error", file,
                                        module, kind, [], msg, SCRIPT))

    base_tags = cocotblib.scan_requirement_tags(base_dir)
    cur_tags = cocotblib.scan_requirement_tags(cur_dir)
    for name in sorted(set(base_tags) | set(cur_tags)):
        if base_tags.get(name) != cur_tags.get(name):
            bad("holdout_req_changed",
                f"{name}: req ids {sorted(base_tags.get(name, []))} -> "
                f"{sorted(cur_tags.get(name, []))}", "holdout", name)

    for bpy in sorted(base_dir.glob("*.py")):
        rel = f"holdout/{bpy.name}"
        cpy = cur_dir / bpy.name
        if not cpy.is_file():
            bad("holdout_file_removed", f"{rel} is gone", rel)
            continue
        b, c = summarise(bpy), summarise(cpy)
        bt = {n for n, f in b["funcs"].items() if f["test"]}
        ct = {n for n, f in c["funcs"].items() if f["test"]}
        for n in sorted(bt ^ ct):
            bad("holdout_tests_changed",
                f"test {n} was {'removed' if n in bt else 'added'}", rel, n)
        if b["consts"] != c["consts"]:
            bad("holdout_bound_changed",
                "a module-level constant changed: "
                f"{_delta(b['consts'], c['consts'])}", rel)
        for name, bf in sorted(b["funcs"].items()):
            cf = c["funcs"].get(name)
            if cf is None:
                if bf["judged"] or bf["model"]:
                    bad("holdout_judgement_changed",
                        f"{name}, which judges or models, was removed",
                        rel, name)
                continue
            if bf["decorators"] != cf["decorators"]:
                bad("holdout_tests_changed",
                    f"{name}: decorator {bf['decorators']} -> "
                    f"{cf['decorators']}", rel, name)
            if bf["judged"] != cf["judged"]:
                bad("holdout_judgement_changed",
                    f"{name}: {_delta(bf['judged'], cf['judged'])}",
                    rel, name)
            if bf["binds"] != cf["binds"]:
                bad("holdout_bound_changed",
                    f"{name}: {_delta(bf['binds'], cf['binds'])}", rel, name)
            if (bf["judged"] or cf["judged"]) and \
                    bf["control"] != cf["control"]:
                bad("holdout_control_changed",
                    f"{name}: {_delta(bf['control'], cf['control'])}",
                    rel, name)
            if bf["model"] and not bf["test"] and bf["model"] != cf["model"]:
                bad("holdout_model_changed",
                    f"{name} is a plain helper (a reference model computes "
                    "expected values) and it changed", rel, name)
    return found


def _delta(before: Counter, after: Counter) -> str:
    gone = sorted((before - after).elements())
    new = sorted((after - before).elements())
    parts = [f"was `{g}`" for g in gone] + [f"now `{n}`" for n in new]
    return "; ".join(parts)[:600]


def baseline_dir(ws: Path, label: str) -> Path:
    if not LABEL_RE.match(label or ""):
        raise CheckError(f"bad snapshot label {label!r}")
    snap = ws / SNAP_DIR / label
    if snap.is_symlink() or (ws / SNAP_DIR).is_symlink():
        raise CheckError(f"{SNAP_DIR}/{label} is a symlink - refused")
    base = snap / "holdout"
    if not base.is_dir() or not any(base.glob("*.py")):
        raise CheckError(f"snapshot {label} has no holdout/*.py to compare "
                         "against - take it with `state.py snapshot` "
                         "before the fixer edits holdout/")
    return base


def check(ws: Path, label: str) -> list[dict]:
    cur = ws / "holdout"
    if not cur.is_dir():
        raise CheckError(f"no holdout/ under {ws}")
    return compare(baseline_dir(ws, label), cur)


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--baseline", required=True,
                    help="state_snapshots/<label> taken before the fix")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    violations = check(ws, args.baseline)
    return (checklib.report(SCRIPT, ws / "holdout", violations,
                            baseline=args.baseline), args.out)


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
