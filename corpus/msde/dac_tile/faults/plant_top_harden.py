"""Fault: an analog pin sent to a pad off the analog template (gates.yaml's
msde top_harden row). Moves vout from ua[0] to ua[6] in interface.yaml:
the fault is the gap below ua[6], because ua[0] to ua[5] are left unwired,
which precheck fails under analog_pins. top_harden must
report ua_pin_off_template before it spends a harden on it. Refuses when
vout is not on ua[0], so a no-op edit can never pass as the fault."""
from pathlib import Path

import yaml


def plant(ws: Path) -> None:
    path = ws / "interface.yaml"
    iface = yaml.safe_load(path.read_text(encoding="utf-8"))
    if (iface.get("ua_pins") or {}) != {"vout": 0}:
        raise RuntimeError(f"plant_top_harden.py: expected ua_pins "
                           f"{{vout: 0}}, found {iface.get('ua_pins')}")
    iface["ua_pins"] = {"vout": 6}
    path.write_text(yaml.safe_dump(iface, sort_keys=False), encoding="utf-8")
