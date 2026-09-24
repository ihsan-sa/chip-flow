# M9 spike: does gLayout run on the image's gdsfactory 9.51?

**Verdict: no. Take the fallback — generator code written directly against
gdsfactory 9.51 with GF180's primitive cells, not gLayout.**

## Upstream tried

`github.com/ReaLLMASIC/gLayout` @ `3e129ede58b4d21509dad682e56b9d573cefe8ab`
(2026-08-28, its own `setup.py` pins `gdsfactory>6.0.0,<=7.7.0`).

## Setup

```
bin/eda python -m venv --system-site-packages .venv
.venv/bin/pip install --no-deps <clone of the commit above>
.venv/bin/pip install --no-deps prettyprint gdstk nltk   # only what system-site lacked
```
`.venv` sees the image's gdsfactory 9.51.0, numpy 2.5.3, klayout 0.30.12 —
all newer than gLayout's own pins.

## What broke

Importing gLayout's default ("gdsfactory") backend fails: 7 of its 14
gdsfactory imports 404 against 9.51 (`component_reference`, `cell`,
`polygon`, `components.rectangle`, `components.rectangular_ring`,
`functions.transformed`, `geometry.boolean` all moved or gone). The deep one
isn't a rename: `ComponentReference` in 9.51 is `kfactory.instance.DInstance`,
a different type from a different library, not the class gLayout's adapter
expects.

This fork also carries a second, native **`gdstk` backend**
(`GLAYOUT_BACKEND=gdstk`, no gdsfactory import at all). Under it,
`nmos()`, `pmos()`, `current_mirror()` and a hand-routed inverter (adapted
from upstream's `tutorial/glayout_tutorial_INV_part1.ipynb`, using GF180's
mapped PDK) all generate GDS with **no patches** — one line dropped
(`ComponentReference.name` has no setter on this backend; cosmetic, not
used). See `docs/spikes/glayout/gen_inverter.py`, `docs/spikes/glayout/gen_mirror.py`.

## DRC / LVS on the gdstk-backend output

`bin/eda magic` (gf180mcuD tech, `drc check`): inverter clean (0 errors);
mirror 1 error, `Metal2 spacing < 0.28um (M2.2a)`.

`bin/eda klayout -b -r $PDK/libs.tech/klayout/tech/drc/gf180mcu.drc`
(the real GF180 signoff deck): **inverter 424 violations, mirror 1004**,
every one tagged `*_OFFGRID`. GF180's manufacturing grid is 0.005 µm
(`geom.rb`: `layer.ongrid(0.005)`); the gdstk backend's `write_gds`/
`flatten` path doesn't snap to it despite `component_snap_to_grid()` being
called on both cells. Magic's own reader silently re-snaps on load (hence
0/1 above), masking the same bug. This is a correctness bug in the
escape-hatch backend, not a version-pin problem, and not one to patch
blindly inside this spike.

`bin/eda netgen -batch lvs` on the inverter: magic-extracted the GDS to
SPICE (`extract all; ext2spice`) and ran it against a copy of itself —
**"Circuits match uniquely."** This only proves the extract→netgen chain
runs on gLayout's output; no reference schematic netlist was wired up in
the time budget, so it is not a real schematic-vs-layout LVS.

## Patches

None to gLayout itself — no patch files beside this doc. The only
adaptation is the one dropped line noted above, inline in
`docs/spikes/glayout/gen_inverter.py`.

## Recommendation for M9

Both routes into gLayout need real fixes before a smoke test would pass:
the default backend needs its adapter rewritten against kfactory-backed
gdsfactory 9.51 (an architecture change, not an import fix), and the
gdstk backend needs a real grid-precision bug found and fixed with
confidence, not guessed at. Given the design doc's own fallback exists and
is smaller in scope (write `layout/gen_<block>.py` straight against
gdsfactory 9.51 and GF180's primitive cells, no adapter layer to keep in
sync with either upstream), M9 should take that fallback rather than sink
further time into gLayout.

## M9 note: the PDK's magic tech file, patched per run

gf180mcuD.tech's cifinput maps GDS 110/11 to MET2RES, among the MET1 lines. Its own cifoutput, klayout's .lyp and layers_def.py all say 110/11 is metal1_res, so I think it's a typo. As shipped, magic never forms rm1 and extracts the resistor as a short. `engine/lib/layoutlib.py` writes a copy of the tech file per run with that one line changed, and it refuses to run if the line isn't there, so a PDK update that fixes or moves it shows up at once. The PDK itself is never edited.
