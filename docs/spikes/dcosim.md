# dcosim spike (M10): does ngspice's d_cosim/ivlng bridge work?

## Verdict

**No — d_cosim's `simulation="ivlng"` backend is broken in this image, and
is not fixable without writing into the read-only toolchain tree.**
M10 should build `cosim` on the first working fallback from design.md
section 5: **cocotbext-ams**, which this image already ships and which
turns out to itself be an implementation of section 5's second fallback
too (a lock-step driver of ngspice's shared library) — so there is only
one fallback to pick, not two.

Open item for M10: a real gf180 transistor-level two-inverter block does
not converge inside that bridge yet (below). The bridge mechanism itself
is proven; the transistor-level convergence work is not.

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

## Reproduce

```
docs/spikes/dcosim/run.sh
```
