# netlist_mismatch (lvs)

netgen found the layout-extracted netlist does not match the post-
synthesis netlist it should implement (an unmatched net/device, a removed
via, a topology or property mismatch). Routes to `harden`.

**Cheapest fix first:** the finding carries netgen's own tail (unmatched
nets/devices, "Circuits do not match" / "Property errors"); lvs_work's
own scratch files under ws/log/lvs_work/ carry the full comparison. A
harden re-run first rules out a stale/partial LibreLane run producing an
inconsistent GDS-vs-netlist pair.

**Trap:** an LVS mismatch is a fabrication-correctness gate, not a style
one - never mark it waived without a human decision (docs/design.md
section 2's "The record of what ran is the release"); if it persists
after a clean re-run, escalate rather than guess at what netgen means.
