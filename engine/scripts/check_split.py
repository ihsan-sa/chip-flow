#!/usr/bin/env python
"""check_split.py - the split gate (docs/design.md 1.5 msde table, "### M10.").

    check_split.py --workspace DIR [--out FILE]

Reads <workspace>/interface.yaml (every crossing signal, with its direction,
level, domain, width and load - docs/design.md section 5: "The splitter
writes interface.yaml, every crossing signal with its direction, level,
domain and load, and two specs carrying the same entries, and split checks
they agree") plus the two side specs that must carry the same entries,
<workspace>/digital_spec.yaml and <workspace>/analog_spec.yaml, each an
`interface:` list of the same shape.

Nested vde/ade workspaces (docs/design.md 1.4's P2 "the two nested runs")
are not built yet at M10 - they arrive once M4 and M9 merge. Until then the
two "specs carrying the same entries" are these two small top-level files,
not a real nested block's spec.yaml `interface:` section; once nested runs
land, this gate should read THOSE instead and these two files retire. That
substitution is out of scope here (docs/design.md, "### M10." boundaries).

Passes when every interface.yaml signal appears, by name, in both
digital_spec.yaml's and analog_spec.yaml's `interface:` lists, with matching
direction, level, domain, width and load - every field is required on an
interface.yaml entry (a name-only entry is refused, not compared None ==
None against an equally bare side entry). Fault this gate must catch
(gates.yaml): "a control word width that differs between the two".

Analog pads (Tiny Tapeout analog tile). An analog pin that leaves the chip
on a ua pad is not a crossing signal: top_harden and precheck route pads
only from interface.yaml's top-level `ua_pins: {<pin>: <k>}` map
(check_top_harden.py), so a pad listed under `signals:` is joined to the
digital side instead and nothing reaches its pad. The analog side declares
its pads as `pads: [<pin>, ...]` in analog_spec.yaml. The gate fails with
`pad_in_signals` when an interface.yaml signal is a ua_pins key, one of the
analog side's `pads`, or named like a pad (`ua`, `ua[k]`, `ua_*`, `uaK`),
and with `pad_missing_from_ua_pins` when a pad the analog side declares is
not in ua_pins. A malformed `ua_pins` (not a mapping) or `pads` (not a list
of names) is refused (exit 2).

CLI/exit contract: checklib's (argparse, JSON to stdout or --out, exit 0
pass, 1 violations, 2 error with a remediation string).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS.parent
sys.path.insert(0, str(ENGINE / "lib"))
import checklib  # noqa: E402
from checklib import CheckError  # noqa: E402

import yaml  # noqa: E402

SCRIPT = "check_split"
INTERFACE_REL = "interface.yaml"
SIDE_SPECS = {"digital": "digital_spec.yaml", "analog": "analog_spec.yaml"}
# The fields a crossing signal carries (docs/design.md section 5: "every
# crossing signal with its direction, level, domain and load"). `width` is
# not named in that sentence, but it is the field gates.yaml's own named
# fault for this gate turns on ("a control word width that differs between
# the two"), so it is checked and required with the same weight as the four
# the prose does name.
SIGNAL_FIELDS = ("direction", "level", "domain", "width", "load")
# A name that is itself a Tiny Tapeout analog pad: ua, ua[k], ua_*, uaK.
UA_PAD_RE = re.compile(r"^ua(\[\d+\]|_.*|\d+)?$", re.I)


def _load_yaml_mapping(path: Path, what: str) -> dict:
    if not path.is_file():
        raise CheckError(f"no {what} at {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CheckError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise CheckError(f"{path} must be a YAML mapping, got "
                         f"{type(data).__name__}")
    return data


def load_interface(path: Path) -> dict[str, dict]:
    """{signal_name: {direction, level, domain, width, load}} from
    interface.yaml. Duplicate, malformed, or incomplete entries are refused
    (CheckError) - interface.yaml is the splitter's own output and an entry
    missing one of its required fields (or carrying only a name) means the
    split step itself did not finish, not a finding this gate should report.
    Without this, an entry with only a name would compare None == None
    against a side spec missing the same fields and pass silently."""
    data = _load_yaml_mapping(path, "interface.yaml")
    signals = data.get("signals")
    if not isinstance(signals, list) or not signals:
        raise CheckError(f"{path}: no non-empty 'signals' list")
    out: dict[str, dict] = {}
    for i, sig in enumerate(signals):
        if not isinstance(sig, dict):
            raise CheckError(f"{path}: signals[{i}] is not a mapping")
        name = sig.get("name")
        if not isinstance(name, str) or not name.strip():
            raise CheckError(f"{path}: signals[{i}] has no non-empty 'name'")
        if name in out:
            raise CheckError(f"{path}: signal {name!r} appears more than once")
        missing = [f for f in SIGNAL_FIELDS if sig.get(f) is None]
        if missing:
            raise CheckError(
                f"{path}: signal {name!r} is missing {', '.join(missing)}")
        out[name] = {k: sig.get(k) for k in SIGNAL_FIELDS}
    return out


def load_side_interface(path: Path, side: str) -> dict[str, dict]:
    """{signal_name: {direction, level, domain, width, load}} from a side
    spec's own `interface:` list - same shape as interface.yaml's, read
    leniently (a side spec missing the whole section, or an entry missing a
    field, is a finding - a mismatch against interface.yaml's now-required
    fields - not a refusal: the splitter may not have reached that side
    yet)."""
    if not path.is_file():
        return {}
    data = _load_yaml_mapping(path, f"{side} spec")
    entries = data.get("interface")
    if not isinstance(entries, list):
        return {}
    out: dict[str, dict] = {}
    for sig in entries:
        if not isinstance(sig, dict):
            continue
        name = sig.get("name")
        if isinstance(name, str) and name.strip():
            out[name] = {k: sig.get(k) for k in SIGNAL_FIELDS}
    return out


def load_ua_pins(path: Path) -> set[str]:
    """Lower-cased pin names of interface.yaml's optional `ua_pins` map -
    the analog pins that leave the chip on a ua pad (check_top_harden.py
    reads the same map). Anything but a mapping is refused."""
    ua = _load_yaml_mapping(path, "interface.yaml").get("ua_pins")
    if ua is None:
        return set()
    if not isinstance(ua, dict):
        raise CheckError(f"{path}: 'ua_pins' must be a mapping "
                         "{<analog pin>: <ua pad index>}")
    return {str(p).lower() for p in ua}


def load_analog_pads(path: Path) -> set[str]:
    """Lower-cased names in analog_spec.yaml's optional `pads:` list - the
    analog pins the analog side says go out on a ua pad."""
    if not path.is_file():
        return set()
    pads = _load_yaml_mapping(path, "analog spec").get("pads")
    if pads is None:
        return set()
    if not isinstance(pads, list) or not all(
            isinstance(p, str) and p.strip() for p in pads):
        raise CheckError(f"{path}: 'pads' must be a list of analog pin names")
    return {p.lower() for p in pads}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="block workspace")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    ws = Path(args.workspace)
    interface_path = ws / INTERFACE_REL
    interface = load_interface(interface_path)

    sides = {side: load_side_interface(ws / rel, side)
             for side, rel in SIDE_SPECS.items()}

    violations = []

    def bad(kind: str, refs: list[str], msg: str):
        violations.append(checklib.violation(
            "split", "error", INTERFACE_REL, None, kind, refs, msg, "check_split"))

    for name, canon in sorted(interface.items()):
        for side, entries in sides.items():
            if name not in entries:
                bad("signal_missing_from_spec", [name],
                   f"{name!r} is in interface.yaml but not in the {side} "
                   f"spec's 'interface' list")
                continue
            side_entry = entries[name]
            for field in SIGNAL_FIELDS:
                want, got = canon.get(field), side_entry.get(field)
                if want != got:
                    kind = f"{field}_mismatch"
                    bad(kind, [name],
                       f"{name!r} {field} disagrees: interface.yaml says "
                       f"{want!r}, the {side} spec says {got!r}")

    interface_names = set(interface)
    for side, entries in sides.items():
        extra = sorted(set(entries) - interface_names)
        for name in extra:
            bad("signal_not_declared", [name],
               f"{name!r} is in the {side} spec's 'interface' list but not "
               "in interface.yaml")

    # A pad is routed from ua_pins only; as a crossing signal it would be
    # joined to the digital side and never reach its pad (task brief: "the
    # split gate fails ... when a pad or ua_* pin appears in interface.yaml's
    # signals, or when an analog pad the analog side declares is missing
    # from ua_pins").
    ua_pins = load_ua_pins(interface_path)
    pads = load_analog_pads(ws / SIDE_SPECS["analog"])
    for name in sorted(interface):
        low = name.lower()
        if low in ua_pins or low in pads or UA_PAD_RE.match(name):
            bad("pad_in_signals", [name],
               f"{name!r} is an analog pad, not a crossing signal: take it "
               "out of interface.yaml's signals (and both side specs' "
               "'interface' lists) and map it in interface.yaml's ua_pins "
               "{<pin>: <ua index>} - top_harden routes pads only from there")
    for name in sorted(pads - ua_pins):
        bad("pad_missing_from_ua_pins", [name],
           f"the analog spec declares pad {name!r} but interface.yaml's "
           f"ua_pins does not map it; add '{name}: <ua index>' under ua_pins "
           "or it never reaches a pad")

    payload = checklib.report(SCRIPT, interface_path, violations,
                              signals=sorted(interface))
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
