"""netlistlib.py - SPICE netlist device scanning for /ade (docs/design.md
1.5, "### M8."). New at M8: no /hwde precedent (hwde's netlists are derived
from a KiCad schematic, never hand/agent-written against a PDK). Backs
check_netlist_lint.py's static checks (known models, floating nodes) and
check_bench_strength.py's device-mutant generator (gates.yaml's own words:
"size doubled, connection removed, type flipped, bias halved").

Device line shape this module understands - every real gf180mcuD PDK
instantiation seen in this repo's own probes and in docs/spikes/dcosim's
netlists follows it: `x<ref> <node> [<node> ...] <model_or_subckt> [k=v
...]`. The model/subckt name is not at a fixed column (a call's node count
depends on the target `.subckt`'s own pin list), so it is found by scanning
back from the end of the line past every trailing `k=v` token - the first
bareword hit that way is the model name, everything between the ref and it
are nodes. Only `x...` lines are scanned; `.subckt`/`.ends`/`.model`/`.param`
and comment/control lines are not devices.
"""
from __future__ import annotations

import re
from pathlib import Path

GLOBAL_NETS = {"0", "gnd", "vss"}
PDK_NGSPICE_FILES = ("sm141064.spice", "sm141064_mim.spice")
# Standard-cell subckts a mixed-signal macro may instantiate as drivers
# (msde's drivers-in-macro split). Optional: a PDK tree without them still
# lints primitives.
PDK_STDCELL_FILES = tuple(
    f"libs.ref/{lib}/spice/{lib}.spice"
    for lib in ("gf180mcu_fd_sc_mcu7t5v0", "gf180mcu_fd_sc_mcu9t5v0"))
SUBCKT_RE = re.compile(r"^\s*\.subckt\s+(\S+)((?:\s+\S+)*)", re.IGNORECASE)
DEVICE_RE = re.compile(r"^\s*[xX](\S+)\s+(.*)$")


def strip_comment(line: str) -> str:
    # SPICE line comments start with '*' at column 1 (a whole-line comment)
    # or '$' / ';' inline - only the whole-line form matters for our
    # device/subckt scans (every real deck here comments a full line, never
    # trails an inline '$').
    return "" if line.lstrip().startswith("*") else line


def join_continuations(netlist_text: str) -> list[str]:
    """netlist_text's lines with every SPICE `+` continuation line folded
    into the line before it. Comment and blank lines are dropped, so a `*`
    line between a header and its `+` lines does not break the join."""
    out: list[str] = []
    for raw in netlist_text.splitlines():
        line = strip_comment(raw)
        if not line.strip():
            continue
        if line.lstrip().startswith("+") and out:
            out[-1] += " " + line.lstrip()[1:].strip()
        else:
            out.append(line)
    return out


def known_models(pdk_root: Path) -> set[str]:
    """Every `.subckt NAME` the PDK's own ngspice model files and its
    standard-cell spice libraries declare - read live off this box's real
    PDK tree, never a hardcoded snapshot that could drift from whatever
    `EDA_TOOLCHAIN` actually points at."""
    names: set[str] = set()
    ngspice_dir = Path(pdk_root) / "libs.tech" / "ngspice"
    found_any = False
    for fname in PDK_NGSPICE_FILES:
        p = ngspice_dir / fname
        if not p.is_file():
            continue
        found_any = True
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = SUBCKT_RE.match(strip_comment(line))
            if m:
                names.add(m.group(1).lower())
    if not found_any:
        from checklib import CheckError
        raise CheckError(f"no PDK ngspice model files found under {ngspice_dir}")
    for rel in PDK_STDCELL_FILES:
        p = Path(pdk_root) / rel
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = SUBCKT_RE.match(line)
            if m:
                names.add(m.group(1).lower())
    return names


def parse_devices(netlist_text: str) -> list[dict]:
    """[{ref, nodes: [...], model, params: {k: v}, line}] for every `x...`
    instantiation line in netlist_text, in file order."""
    out = []
    for lineno, raw in enumerate(netlist_text.splitlines(), 1):
        line = strip_comment(raw)
        m = DEVICE_RE.match(line)
        if not m:
            continue
        ref = "x" + m.group(1)
        tokens = m.group(2).split()
        if not tokens:
            continue
        i = len(tokens) - 1
        while i > 0 and "=" in tokens[i]:
            i -= 1
        model = tokens[i]
        nodes = tokens[:i]
        params = {}
        for tok in tokens[i + 1:]:
            if "=" in tok:
                k, _, v = tok.partition("=")
                params[k.lower()] = v
        out.append({"ref": ref, "nodes": nodes, "model": model.lower(),
                   "params": params, "line": lineno})
    return out


def subckt_pins(netlist_text: str) -> dict[str, list[str]]:
    """{subckt_name.lower(): [pin, ...]} for every `.subckt NAME p1 p2 ...`
    THIS netlist file itself declares - used to exclude a subckt's own
    header pins from the floating-node scan (they are meant to connect once
    inside, many times outside)."""
    out = {}
    for line in join_continuations(netlist_text):
        m = SUBCKT_RE.match(line)
        if m:
            out[m.group(1).lower()] = m.group(2).split()
    return out


def floating_nodes(devices: list[dict], declared_pins: set[str]) -> list[str]:
    """Nodes touched by exactly one device terminal across the whole
    netlist - excluding GLOBAL_NETS (ground/supply rails, expected to fan
    out to zero or one terminal in a tiny corpus bench) and declared_pins
    (a subckt's own header pins, meant to connect externally)."""
    counts: dict[str, int] = {}
    for dev in devices:
        for n in dev["nodes"]:
            key = n.lower()
            if key in GLOBAL_NETS or key in declared_pins:
                continue
            counts[key] = counts.get(key, 0) + 1
    return sorted(n for n, c in counts.items() if c < 2)


# ------------------------------------------------------------- device mutants
# bench_strength (gates.yaml, "### M8."): "faults.py device mutants (size
# doubled, connection removed, type flipped, bias halved)". One mutant
# changes exactly ONE aspect of ONE declared device; every mutant must push
# at least one bench measure out of its bound (a survivor is the fault:
# "bounds wide enough to pass anything").

def _flip_target(model: str) -> str | None:
    """The opposite-type MOS model at the SAME voltage rating (a 3.3V nfet
    flips to the 3.3V pfet, never to a 6V device) - "type flipped" must stay
    a same-rail swap, or the mutant would also be a supply/model mismatch
    the netlist_lint gate would refuse before bench_strength ever ran it."""
    pairs = {"nfet_03v3": "pfet_03v3", "pfet_03v3": "nfet_03v3",
            "nfet_05v0": "pfet_05v0", "pfet_05v0": "nfet_05v0",
            "nfet_06v0": "pfet_06v0", "pfet_06v0": "nfet_06v0"}
    return pairs.get(model)


SOURCE_RE = re.compile(r"^\s*([iIvV])(\S+)\s+(\S+)\s+(\S+)\s+(?:dc\s+)?"
                       r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*$")


def parse_sources(bench_text: str) -> dict[str, dict]:
    """{ref.lower(): {ref, kind: i|v, p, n, value, line}} for every plain
    `i<ref>`/`v<ref> p n [dc] VALUE` bias source in a bench file - the
    reference/tail current or voltage a mirror-style bench declares, which
    lives in tb/, never in netlist/ (netlistlib.parse_devices only sees
    `x...` subcircuit instances)."""
    out = {}
    for lineno, raw in enumerate(bench_text.splitlines(), 1):
        m = SOURCE_RE.match(strip_comment(raw))
        if m:
            kind, ref, p, n, val = m.groups()
            out[(kind.lower() + ref).lower()] = {
                "ref": kind.lower() + ref, "kind": kind.lower(), "p": p,
                "n": n, "value": val, "line": lineno}
    return out


def device_mutants(netlist_text: str, device_refs: list[str],
                   bench_text: str | None = None) -> list[dict]:
    """[{id, ref, kind, target: netlist|bench, describe, apply(text) ->
    text}] - one entry per (declared device ref, mutation kind) combination.
    `kind` is one of gates.yaml's four named classes for bench_strength:
    size_doubled/connection_removed (a device's own W or a terminal, from
    netlist_text's `x...` lines), type_flipped (an nfet<->pfet swap at the
    same voltage rating, netlist_text), bias_halved (a plain `i`/`v` source
    declared in bench_text, e.g. a mirror's reference current)."""
    devices = {d["ref"]: d for d in parse_devices(netlist_text)}
    sources = parse_sources(bench_text) if bench_text else {}
    mutants: list[dict] = []
    for ref in device_refs:
        dev = devices.get(ref)
        if dev is not None:
            model = dev["model"]
            if "w" in dev["params"]:
                mutants.append(_size_mutant(ref, dev, "w"))
            if "l" in dev["params"] and "w" not in dev["params"]:
                mutants.append(_size_mutant(ref, dev, "l"))
            if len(dev["nodes"]) >= 2:
                mutants.append(_disconnect_mutant(ref, dev))
            flip = _flip_target(model)
            if flip:
                mutants.append(_retype_mutant(ref, dev, flip))
            continue
        src = sources.get(ref.lower())
        if src is not None:
            mutants.append(_bias_mutant(ref, src))
    return mutants


def _replace_line(text: str, lineno: int, new_line: str) -> str:
    lines = text.splitlines(keepends=True)
    ending = "\n" if lines[lineno - 1].endswith("\n") else ""
    lines[lineno - 1] = new_line + ending
    return "".join(lines)


def _rebuild_device_line(dev: dict, model: str, nodes: list[str],
                         params: dict[str, str]) -> str:
    parts = [dev["ref"], *nodes, model,
            *(f"{k}={v}" for k, v in params.items())]
    return " ".join(parts)


def _size_mutant(ref: str, dev: dict, key: str) -> dict:
    def apply(text: str) -> str:
        params = dict(dev["params"])
        try:
            params[key] = repr(float(params[key]) * 2.0)
        except ValueError:
            # a brace expression or an SI-suffixed value ("4u"): ngspice
            # reads a bare `{w_out}*2` or `4u*2` as the value alone and drops
            # the `*2`, so the doubling must sit inside one expression
            inner = params[key].strip()
            if inner.startswith("{") and inner.endswith("}"):
                inner = inner[1:-1]
            params[key] = "{(" + inner + ")*2}"
        new_line = _rebuild_device_line(dev, dev["model"], dev["nodes"], params)
        return _replace_line(text, dev["line"], new_line)
    return {"id": f"{ref}_size_doubled_{key}", "ref": ref, "kind": "size_doubled",
           "target": "netlist", "describe": f"{ref}: {key} doubled",
           "apply": apply}


def _disconnect_target(dev: dict) -> tuple[int, str]:
    """(index, name) of the terminal a connection_removed mutant floats.
    Normally the last one (a MOSFET's bulk in d g s b order). When a
    4-terminal device's bulk is the same node as its own source (compared
    case-insensitively, as spice does), floating the bulk changes nothing a
    simulation can see, so the drain (first terminal) is floated instead - a
    stronger mutant, not an exclusion (owner ruling, 2026-09-25)."""
    nodes = dev["nodes"]
    if len(nodes) == 4 and nodes[3].lower() == nodes[2].lower():
        return 0, "drain (bulk tied to its own source)"
    if len(nodes) == 4:
        return 3, "bulk"
    return len(nodes) - 1, "last terminal"


def _disconnect_mutant(ref: str, dev: dict) -> dict:
    idx, name = _disconnect_target(dev)

    def apply(text: str) -> str:
        nodes = list(dev["nodes"])
        nodes[idx] = f"__floating_{ref}__"
        new_line = _rebuild_device_line(dev, dev["model"], nodes, dev["params"])
        return _replace_line(text, dev["line"], new_line)
    return {"id": f"{ref}_connection_removed", "ref": ref,
           "kind": "connection_removed", "target": "netlist",
           "describe": f"{ref}: {name} disconnected", "apply": apply}


def _retype_mutant(ref: str, dev: dict, flip_model: str) -> dict:
    def apply(text: str) -> str:
        new_line = _rebuild_device_line(dev, flip_model, dev["nodes"], dev["params"])
        return _replace_line(text, dev["line"], new_line)
    return {"id": f"{ref}_type_flipped", "ref": ref, "kind": "type_flipped",
           "target": "netlist",
           "describe": f"{ref}: {dev['model']} -> {flip_model}", "apply": apply}


def _bias_mutant(ref: str, src: dict) -> dict:
    def apply(text: str) -> str:
        half = repr(float(src["value"]) / 2.0)
        new_line = f"{src['ref']} {src['p']} {src['n']} dc {half}"
        return _replace_line(text, src["line"], new_line)
    return {"id": f"{ref}_bias_halved", "ref": ref, "kind": "bias_halved",
           "target": "bench", "describe": f"{ref}: bias halved",
           "apply": apply}
