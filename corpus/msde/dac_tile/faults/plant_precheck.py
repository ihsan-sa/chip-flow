"""Fault: an analog pad info.yaml claims with nothing wired to it
(gates.yaml's msde precheck row). Runs top_harden on the untouched design,
then adds a ua[1] pin to the assembled top's spec, which is what precheck's
info.yaml is written from - analog_pins becomes 2 while the hardened GDS
still has metal on ua[0] only. Refuses when top_harden does not pass, so a
planted fault can never ride on a broken harden."""
from pathlib import Path

import yaml


def plant(ws: Path) -> None:
    import check_top_harden
    payload, _out = check_top_harden.run(["--workspace", str(ws)])
    if payload.get("status") != "pass":
        raise RuntimeError(f"plant_precheck.py: top_harden did not pass on "
                           f"the untouched design: {payload.get('violations')}")
    path = ws / "top" / "spec" / "spec.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    ua = spec["macros"][0].get("ua") or {}
    if sorted(ua.values()) != [0]:
        raise RuntimeError(f"plant_precheck.py: expected one pin on ua[0], "
                           f"found {ua}")
    ua["unwired"] = 1
    spec["macros"][0]["ua"] = ua
    path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
