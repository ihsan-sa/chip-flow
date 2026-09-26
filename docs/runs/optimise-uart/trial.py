"""usage: U10_WS=<ws> eda python trial.py NOTE OLD1 NEW1 [OLD2 NEW2 ...]
One optimiser change: apply the replacements to rtl/uart_tx.v, run
`optimise.py trial --note NOTE`, print a one-line summary."""
import json, subprocess, sys
from pathlib import Path
import os
W = Path(os.environ['U10_WS']); T = W / 'rtl/uart_tx.v'
REPO = Path(__file__).resolve().parents[3]
note, pairs = sys.argv[1], sys.argv[2:]
text = T.read_text()
for old, new in zip(pairs[::2], pairs[1::2]):
    assert text.count(old) == 1, f"not found once: {old!r}"
    text = text.replace(old, new)
T.write_text(text)
r = subprocess.run([str(REPO / 'bin/eda'), 'python', 'engine/scripts/optimise.py', 'trial',
                    '--workspace', str(W), '--note', note], cwd=REPO, capture_output=True, text=True)
d = json.loads(r.stdout)
print(r.returncode, {k: d.get(k) for k in ('trial', 'kept', 'area', 'slack', 'power', 'stop')},
      {k: v for k, v in (d.get('constraints') or {}).items() if v != 'pass'},
      [x[:300] for x in d.get('detail', [])][:3], d.get('error'))
