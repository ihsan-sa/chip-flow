"""M10 resolution of the spike's open issue (dcosim.md): the same
two_inv_gf180.sp transistor-level pair, converging through cocotbext-ams.
The `.ic` fix is `x1.mid` (not `mid` - see that file's own updated
comment) passed via AnalogBlock's `extra_lines`, matching how a real
check_cosim.py bench (corpus/msde/ring_osc_div) would carry PDK includes
and initial conditions.

Real assertions on the digitized readback, not exit 0 - "don't trust exit
0, ngspice exits 0 on many failures" (CLAUDE.md)."""
import os
from pathlib import Path

import cocotb
from cocotb.triggers import Timer
from cocotbext.ams import AnalogBlock, DigitalPin, MixedSignalBridge

HERE = Path(__file__).resolve().parent
LIBNGSPICE = os.environ["LIBNGSPICE_PATH"]
GF180_NGSPICE = os.environ["GF180_NGSPICE_DIR"]


@cocotb.test()
async def test_two_inv_gf180_converges(dut):
    block = AnalogBlock(
        name="two_inv",
        spice_file=str(HERE / "two_inv_gf180.sp"),
        subcircuit="two_inv",
        digital_pins={
            "in_pin": DigitalPin(direction="input", vdd=3.3, vss=0.0),
            "out_pin": DigitalPin(direction="output", vdd=3.3, vss=0.0),
        },
        vdd=3.3,
        vss=0.0,
        tran_step="0.5n",
        extra_lines=[
            f".include '{GF180_NGSPICE}/design.spice'",
            f".lib '{GF180_NGSPICE}/sm141064.spice' typical",
            # the fix: "x1." is the wrapper netlist's own instance name for
            # the user subcircuit (engine/scripts/... will pass the same
            # kind of prefix for any block with an internal node to seed).
            ".ic v(x1.mid)=3.3 v(out_pin)=0",
        ],
    )
    bridge = MixedSignalBridge(
        dut, [block], max_sync_interval_ns=10.0, simulator_lib=LIBNGSPICE,
    )

    dut.in_pin.value = 0
    sim_task = cocotb.start_soon(bridge.start(duration_ns=50.0))

    await Timer(20, unit="ns")
    dut._log.info("in_pin=0 -> out_pin=%s", dut.out_pin.value)
    assert int(dut.out_pin.value) == 0, "two inversions of 0 must read back 0"

    dut.in_pin.value = 1
    await Timer(20, unit="ns")
    dut._log.info("in_pin=1 -> out_pin=%s", dut.out_pin.value)
    assert int(dut.out_pin.value) == 1, (
        "the digital side must see a real transistor-level transition, not "
        "a stub value")

    await sim_task
    await bridge.stop()
