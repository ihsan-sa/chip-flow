# load_mismatch (split)

A signal's `load` differs between `interface.yaml` and a side spec. The
driver was sized for a different load than the receiver presents.

**Cheapest fix first:** describe the load the receiver really presents
(the digital input it drives, or the analog node it biases) and copy the
same string to all three files.

**Trap:** `load` is free text and compared exactly; keep one wording.
