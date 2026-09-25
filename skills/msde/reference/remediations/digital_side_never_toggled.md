# digital_side_never_toggled (cosim)

`digital_toggles` is zero or missing: the digital side never acted on the
analog waveform. The corpus fault that exercises this is a ring
oscillator that never starts.

**Cheapest fix first:** is the analog output moving at all (an enable held
off, a missing or ignored `.ic`)? Is the crossing signal wired to the
digital input `interface.yaml` names? Is the threshold the bridge
digitizes at inside the analog swing?

**Trap:** a digital-side defect (a counter held in reset) looks the same.
If the analog node moves and the digital input sees edges, the defect is
the digital side's: report it for that side's own router.
