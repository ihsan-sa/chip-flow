# dcosim spike (M10): does ngspice's d_cosim/ivlng bridge work?

## Verdict

**No — d_cosim's `simulation="ivlng"` backend is broken in this image, and
is not fixable without writing into the read-only toolchain tree.**
M10 should build `cosim` on the first working fallback from design.md
section 5: **cocotbext-ams**, which this image already ships and which
turns out to itself be an implementation of section 5's second fallback
too (a lock-step driver of ngspice's shared library) — so there is only
one fallback to pick, not two.

Open item this spike left: a real gf180 transistor-level two-inverter block
did not converge inside that bridge (below). Resolved for M10 — see
"Resolved for M10" below: a wrong node name in the `.ic` fix, not a real
convergence limit. A separate, narrower shared-library timestep quirk
remains and is worked around, not fixed (same section).

## Why ivlng is broken

`ivlng.so`/`ivlng.vpi` exist at
`$T/foss/tools/ngspice/lib/ngspice/`. Two problems, both hardcoded
absolute `/foss/...` paths baked into compiled binaries at image build
time, from before the tree was relocated under `~/.cc/toolchains`:

1. ngspice's own `spinit` (found via `SPICE_LIB_DIR`) hardcodes
   `codemodel /foss/tools/ngspice/lib/ngspice/*.cm` — worked around with a
   corrected local `spinit` pointing at `$T`'s real paths (see `run.sh`).
2. `d_cosim`'s `simulation="ivlng"` keyword itself resolves to the
   hardcoded path `/foss/tools/ngspice/lib/ngspice/ivlng.so` — worked
   around by passing ivlng's real, absolute path instead of the keyword.
3. But `ivlng.so`'s *own* `dlopen()` of `libvvp.so` is **also** hardcoded
   to `/foss/tools/ngspice/lib/ngspice/libvvp.so` — a file that was never
   there; the real one is under `iverilog/lib/`. This is a `dlopen()` of a
   literal compiled-in string, so no env var reaches it. An LD_PRELOAD
   interposer was tried (same trick `bin/eda` uses for yosys's
   `/proc/self/exe`); it failed because `digital.cm` binds the *versioned*
   symbol `dlopen@GLIBC_2.34`, which a plain preload does not intercept.
   Fixing this needs either a symbol-versioned interposer (real effort,
   not attempted further) or a write into the read-only image — out of
   bounds. Confirmed broken, not merely unconfigured.

## The fallback: cocotbext-ams

`eda python3 -c "import cocotbext.ams"` succeeds. It wraps `libngspice.so`
via `ctypes` and lets a normal cocotb testbench drive/read an
`AnalogBlock` (a `.subckt` in a SPICE file) through `MixedSignalBridge`,
event-driven, no Icarus-inside-ngspice involved at all.

`docs/spikes/dcosim/run.sh` runs both halves. The working example: a
digital stub (`two_inv_stub.v`) with cocotb driving `in_pin`, wired to an
ideal two-stage inverting SPICE block (`two_inv.sp`, behavioral E-sources
— numerically well-posed, used to isolate the bridge itself from
transistor convergence). Result, three separate transitions each with a
real ngspice transient in between:

```
40.00ns  in_pin=0 -> out_pin=0
120.00ns in_pin=1 -> out_pin=1
180.00ns in_pin=0 -> out_pin=0
TESTS=1 PASS=1 FAIL=0
```

Each readback comes from cocotb re-reading a digitized ngspice node
voltage after a real transient segment, not a wire — proven separately by
first running a passive R-divider block, which read back a threshold-
dependent value rather than a trivial passthrough.

A real gf180 transistor-level pair is in `two_inv_gf180.sp`. Under this
same bridge (`uic`-started transient, `external` sources) it aborts
around t=5-10ns, before the first input change, with `Timestep too small
... trouble with node "v_vss#branch"`, insensitive to `gmin`/`itl4`
stepping, RC input damping, and correct `.ic` values — a real ngspice
convergence issue with the bridge's cold `uic` start on active devices,
left as an M10 follow-up rather than solved here.

## Resolved for M10

The "correct `.ic` values" above were never actually applied. `mid` is an
internal node of `two_inv_gf180.sp`'s `.subckt` — not a port — and
cocotbext-ams's generated wrapper netlist always instantiates the user
subcircuit as `x1`, so the node's real top-level name is `x1.mid`. ngspice
does not error on an `.ic` naming a node that does not exist; it prints
"IC on non-existent node - mid, ignored" and carries on, so the fix looked
like a no-op rather than a wrong name.

With `.ic v(x1.mid)=<expected> v(out_pin)=<expected>` passed through
`AnalogBlock`'s `extra_lines` (alongside the PDK's own `design.spice` and
`sm141064.spice typical` includes), the identical netlist converges
cleanly under plain batch `ngspice -b` for a full window with real digital
transitions, and converges through cocotbext-ams itself for a real,
digital-driven transition — verified with an assertion on the digitized
readback (`docs/spikes/dcosim/run.sh` section 3, `test_two_inv_gf180.py`),
not exit 0.

A second, narrower issue remains and is not a netlist or modeling problem:
this ngspice build's shared-library transient can drive its own adaptive
timestep to numeric underflow whenever it is forced to land exactly on a
boundary time — a periodic fallback sync, or the analysis's own declared
final `tstop` — regardless of the circuit's actual state at that instant
(confirmed by sweeping about twenty different declared stop times on the
identical static, never-toggling netlist: all but one failed, each exactly
at its own declared end; the same netlist run start-to-finish under plain
batch `ngspice -b`, which never needs to land on such a boundary, never
failed once). This is specific to running through the shared library,
which every real cosim bridge must use (batch mode has no bidirectional
channel) — not to gf180, BSIM, or `uic` specifically. Worked around, not
fixed: a bench requests more duration than it actually needs to observe,
and lets the run finish naturally rather than forcing an early halt
(halting a foreground shared-library `tran` mid-flight was not found to be
reliable either — `bg_halt` targets a `bg_run`-started analysis, not one
started with the plain blocking `tran` command cocotbext-ams's own
`run_simulation()` issues).

M10's own `cosim` gate (`engine/scripts/check_cosim.py`) does not trust a
clean exit for this reason: it parses the raw sim log for ngspice's own
non-convergence signatures independently of what the cocotb regression
reports, on every run.

The `cosim` gate's own bench (`corpus/msde/ring_osc_div`) does not reuse
this gf180 pair — a multi-stage active ring oscillator is a substantially
harder cold-start problem (an unforced symmetric equilibrium, on top of
the same BSIM sensitivity), and gates.yaml's own pass criterion for
`cosim` is a measure inside its bound, not a proof that a gf180 ring
converges. That rung's `ring5.sp` is a real SPICE feedback oscillator built
from ideal RC/comparator stages instead, deliberately avoiding BSIM's
near-zero-leakage cold-start sensitivity while still being genuinely
computed by ngspice — see that rung's own `spec.md` for the reasoning, and
`skills/ade/reference/topologies/` (M8, in flight) for where a real gf180
ring belongs once it exists.

## Reproduce

```
docs/spikes/dcosim/run.sh
```
