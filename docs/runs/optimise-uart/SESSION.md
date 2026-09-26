# The optimise loop on the corpus UART (M7)

A session ran `optimise.py` on `corpus/vde/uart`'s `rtl/uart_tx.v` with
`--objective area --trials 12`, acting as the optimiser: one change per
trial, each scored by `optimise.py trial`. `replay.sh` reruns the same
twelve changes on a fresh copy of the rung, and `trials.tsv` is that
replay's log (the first pass found a bug, below, so the replay is the
clean record).

- Trial 0, the corpus RTL: area 3197.8 um², slack 16.60 ns at 20 ns.
- Six trials were kept. The first (drop the `bits_left` frame counter) cut
  area to 2622.0 with slack 16.84 ns. The winner, trial 10, has area
  2249.5 (-30%), slack 16.48 ns and power 1.39 mW against 2.33 mW.
- Trial 7 failed `sim` and trial 11 failed `lint`, and neither was kept.
  Trial 11 also edited `tb/`, and the loop reverted that file.
- Trials 3, 6, 9 and 12 passed every constraint but did not beat the best
  area, so they were reverted.
- `finish` ran holdout, formal at the spec's depth and mutate on trial 10,
  and all three passed. `uart_tx.winner.v` is that RTL and `winner.diff`
  the change from the corpus.

The bug the first pass found: `state.json.lock`, which state.py writes,
was being reverted as an edit outside the target on every trial. It is now
an engine output, and `test_rtl_edit_outside_target_is_reverted` checks it.
