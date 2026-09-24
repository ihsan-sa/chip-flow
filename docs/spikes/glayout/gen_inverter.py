#!/usr/bin/env python3
"""M9 spike: an NMOS/PMOS inverter, hand-routed from gLayout's primitives on
GF180, adapted from upstream's tutorial/glayout_tutorial_INV_part1.ipynb.

The notebook's own imports (`from gdsfactory import Component`,
`from gdsfactory.cell import cell`, `from gdsfactory.components import
rectangle`) do not resolve against the image's gdsfactory 9.51 -- see
docs/spikes/glayout.md. This script goes through gLayout's own backend
abstraction instead (`glayout.backend`, GLAYOUT_BACKEND=gdstk), which is
what the elementary cells (current_mirror, etc.) already use, and which
imports cleanly. The placement/routing recipe itself is upstream's,
unchanged.
"""
import sys

from glayout.backend import Component, cell, rectangle
from glayout.pdk.gf180_mapped.gf180_mapped import gf180_mapped_pdk as gf180
from glayout.primitives.fet import nmos, pmos
from glayout.primitives.via_gen import via_stack
from glayout.util.comp_utils import evaluate_bbox, align_comp_to_port
from glayout.util.port_utils import rename_ports_by_orientation
from glayout.util.snap_to_grid import component_snap_to_grid
from glayout.routing.straight_route import straight_route
from glayout.routing.c_route import c_route


@cell
def inverter(
    pdk,
    placement: str = "horizontal",
    width: tuple = (3, 3),
    length: tuple = (None, None),
    fingers: tuple = (1, 1),
    multipliers: tuple = (1, 1),
    dummy_1: tuple = (True, True),
    dummy_2: tuple = (True, True),
    tie_layers1: tuple = ("met2", "met1"),
    tie_layers2: tuple = ("met2", "met1"),
    sd_rmult: int = 1,
    **kwargs,
) -> Component:
    pdk.activate()
    top_level = Component(name="inverter")

    fet_P = pmos(pdk, width=width[0], fingers=fingers[0], multipliers=multipliers[0],
                 with_dummy=dummy_1, with_substrate_tap=False, length=length[0],
                 tie_layers=tie_layers1, sd_rmult=sd_rmult, **kwargs)
    fet_N = nmos(pdk, width=width[1], fingers=fingers[1], multipliers=multipliers[1],
                 with_dummy=dummy_2, with_substrate_tap=False, length=length[1],
                 tie_layers=tie_layers2, sd_rmult=sd_rmult, with_dnwell=False, **kwargs)

    fet_P_ref = top_level << fet_P
    fet_N_ref = top_level << fet_N
    # gLayout's gdstk backend's ComponentReference.name has no setter (the
    # gdsfactory backend's does); cosmetic only, so just drop it rather than
    # patch the backend for a debug label.

    ref_dimensions = evaluate_bbox(fet_N)
    if placement == "horizontal":
        fet_N_ref.movex(fet_P_ref.xmax + (ref_dimensions[0] / 2) + pdk.util_max_metal_seperation() + 1)
    elif placement == "vertical":
        fet_N_ref.movey(fet_P_ref.ymin - ref_dimensions[1] / 2 - pdk.util_max_metal_seperation() - 1)
    else:
        raise ValueError("placement must be 'horizontal' or 'vertical'")

    viam2m3 = via_stack(pdk, "met2", "met3", centered=True)
    drain_P_via = top_level << viam2m3
    source_P_via = top_level << viam2m3
    gate_P_via = top_level << viam2m3
    drain_N_via = top_level << viam2m3
    gate_N_via = top_level << viam2m3

    drain_P_via.move(fet_P_ref.ports["multiplier_0_drain_W"].center).movex(-1.5)
    drain_N_via.move(fet_N_ref.ports["multiplier_0_drain_W"].center).movex(-1.5)
    source_P_via.move(fet_P_ref.ports["multiplier_0_source_E"].center).movex(1.5)
    gate_P_via.move(fet_P_ref.ports["multiplier_0_gate_E"].center).movex(1)
    gate_N_via.move(fet_N_ref.ports["multiplier_0_gate_E"].center).movex(1)

    top_level << straight_route(pdk, fet_P_ref.ports["multiplier_0_gate_E"], gate_P_via.ports["bottom_met_N"])
    top_level << straight_route(pdk, fet_N_ref.ports["multiplier_0_gate_E"], gate_N_via.ports["bottom_met_W"])
    top_level << straight_route(pdk, fet_P_ref.ports["multiplier_0_source_E"], source_P_via.ports["bottom_met_W"])
    top_level << straight_route(pdk, fet_P_ref.ports["multiplier_0_drain_W"], drain_P_via.ports["bottom_met_E"])
    top_level << straight_route(pdk, fet_N_ref.ports["multiplier_0_drain_W"], drain_N_via.ports["bottom_met_E"])

    if placement == "horizontal":
        top_level << c_route(pdk, gate_P_via.ports["top_met_S"], gate_N_via.ports["top_met_S"],
                              extension=1.2 * max(width[0], width[1]), width1=0.32, width2=0.32,
                              cwidth=0.32, e1glayer="met3", e2glayer="met3", cglayer="met2")
        top_level << c_route(pdk, drain_P_via.ports["top_met_N"], drain_N_via.ports["top_met_N"],
                              extension=1.2 * max(width[0], width[1]), width1=0.32, width2=0.32,
                              cwidth=0.32, e1glayer="met3", e2glayer="met3", cglayer="met2")
    else:
        top_level << straight_route(pdk, gate_P_via.ports["top_met_S"], gate_N_via.ports["top_met_S"])
        top_level << straight_route(pdk, drain_P_via.ports["top_met_N"], drain_N_via.ports["top_met_N"])

    try:
        top_level << straight_route(pdk, fet_N_ref.ports["multiplier_0_source_W"],
                                     fet_N_ref.ports["tie_W_top_met_W"],
                                     glayer1=tie_layers2[1], fullbottom=True)
    except KeyError:
        pass

    top_level.add_ports(fet_P_ref.get_ports_list(), prefix="P_")
    top_level.add_ports(fet_N_ref.get_ports_list(), prefix="N_")
    top_level.add_ports(drain_P_via.get_ports_list(), prefix="P_drain_")
    top_level.add_ports(source_P_via.get_ports_list(), prefix="P_source_")
    top_level.add_ports(gate_P_via.get_ports_list(), prefix="P_gate_")
    top_level.add_ports(drain_N_via.get_ports_list(), prefix="N_drain_")
    top_level.add_ports(gate_N_via.get_ports_list(), prefix="N_gate_")

    return component_snap_to_grid(rename_ports_by_orientation(top_level))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "inverter.gds"
    comp = inverter(gf180)
    comp.name = "INV"
    comp.write_gds(out)
    print(f"wrote {out}: {len(comp.get_ports_list())} ports")
