"""The cosim bench for ring_osc_div (docs/design.md 1.5 msde table's
`cosim` row, "### M10."). Drives ring5.sp (a real SPICE feedback
oscillator) through cocotbext-ams's MixedSignalBridge, lets the real
divide-by-2 flip-flop (ring_osc_div_top.v) react to the forced waveform,
and records what it measured to reports/cosim_measures.json BEFORE any
assertion that could raise - engine/scripts/check_cosim.py is the gate's
own authority on pass/fail (it parses this file and the raw sim log
independently of whatever this test asserts), but a bench that cannot even
write a measures file is itself the strongest signal something upstream
never ran.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import cocotb
from cocotb.triggers import RisingEdge, SimTimeoutError, with_timeout
from cocotbext.ams import AnalogBlock, DigitalPin, MixedSignalBridge

HERE = Path(__file__).resolve().parent
LIBNGSPICE = os.environ["LIBNGSPICE_PATH"]

EDGES_WANTED = 8
EDGE_TIMEOUT_NS = 400
DURATION_NS = 700.0

# Staggered .ic across the ring's five stages (see ring5.sp's own comment):
# the cocotbext-ams wrapper netlist always instantiates the user subcircuit
# as "x1", so an internal (non-port) node's .ic needs that prefix - a bare
# "v(s0)" would silently apply to nothing (docs/spikes/dcosim.md: this is
# exactly the bug that made the spike's own gf180 .ic attempt a no-op).
RING_IC = [
    ".ic v(x1.s0)=0 v(x1.s1)=3.3 v(x1.s2)=0 v(x1.s3)=3.3 v(x1.s4)=0",
    ".ic v(x1.l0)=0 v(x1.l1)=3.3 v(x1.l2)=0 v(x1.l3)=3.3 v(x1.l4)=0",
]


@cocotb.test()
async def test_ring_osc_div(dut):
    block = AnalogBlock(
        name="ring5",
        spice_file=str(HERE / "ring5.sp"),
        subcircuit="ring5",
        digital_pins={
            "osc_out": DigitalPin(direction="output", vdd=3.3, vss=0.0),
        },
        vdd=3.3,
        vss=0.0,
        tran_step="0.1n",
        extra_lines=list(RING_IC),
    )
    bridge = MixedSignalBridge(
        dut, [block], max_sync_interval_ns=20.0, simulator_lib=LIBNGSPICE,
    )
    sim_task = cocotb.start_soon(bridge.start(duration_ns=DURATION_NS))

    edges_ns: list[float] = []
    try:
        for _ in range(EDGES_WANTED):
            await with_timeout(RisingEdge(dut.clk_div), EDGE_TIMEOUT_NS, "ns")
            edges_ns.append(float(cocotb.utils.get_sim_time(unit="ns")))
    except SimTimeoutError:
        pass  # digital_toggles below tells check_cosim.py exactly how far it got
    finally:
        periods = [b - a for a, b in zip(edges_ns, edges_ns[1:])]
        divided_freq_hz = (
            1.0e9 / (sum(periods) / len(periods)) if len(periods) >= 2 else None
        )
        measures = {
            "divided_freq_hz": divided_freq_hz,
            "digital_toggles": len(edges_ns),
            "edge_times_ns": edges_ns,
        }
        reports_dir = HERE.parent / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "cosim_measures.json").write_text(
            json.dumps(measures, indent=2), encoding="utf-8")
        # duration_ns has enough headroom past EDGES_WANTED edges that this
        # always completes naturally (docs/spikes/dcosim.md: forcing an
        # early halt of a foreground shared-library `tran` does not reliably
        # interrupt it) - the never-toggled fault path above still finishes
        # quickly, since ngspice itself keeps computing a tiny, cheap
        # circuit regardless of what the digital side saw.
        await sim_task
        await bridge.stop()

    assert len(edges_ns) >= EDGES_WANTED, (
        f"clk_div only toggled {len(edges_ns)} time(s) - the digital side "
        "never acted on the analog waveform")
    assert divided_freq_hz is not None
