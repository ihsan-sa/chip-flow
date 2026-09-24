#!/usr/bin/env python3
"""M9 spike: current mirror on GF180 via gLayout's own elementary cell.

Run with the "gdstk" backend (GLAYOUT_BACKEND=gdstk) -- gLayout's
gdsfactory backend does not import against the image's gdsfactory 9.51;
see docs/spikes/glayout.md.
"""
import sys

from glayout.pdk.gf180_mapped.gf180_mapped import gf180_mapped_pdk
from glayout.cells.elementary.current_mirror.current_mirror import current_mirror

out = sys.argv[1] if len(sys.argv) > 1 else "mirror.gds"
comp = current_mirror(gf180_mapped_pdk, numcols=2)
comp.name = "MIRROR"
comp.write_gds(out)
print(f"wrote {out}: {len(comp.get_ports_list())} ports")
