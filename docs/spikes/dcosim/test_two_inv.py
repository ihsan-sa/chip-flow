"""cocotbext-ams fallback proof: cocotb drives an analog two-inverter
SPICE block through ngspice's shared library (no d_cosim/ivlng involved)
and checks the digitized readback really followed the analog computation
across three separate value changes."""
import os

import cocotb
from cocotb.triggers import Timer
from cocotbext.ams import AnalogBlock, DigitalPin, MixedSignalBridge

HERE = os.path.dirname(__file__)
LIBNGSPICE = os.environ["LIBNGSPICE_PATH"]


@cocotb.test()
async def test_two_inv_tracks_input(dut):
    block = AnalogBlock(
        name="two_inv",
        spice_file=os.path.join(HERE, "two_inv.sp"),
        subcircuit="two_inv",
        digital_pins={
            "in_pin": DigitalPin(direction="input", vdd=3.3, vss=0.0),
            "out_pin": DigitalPin(direction="output", vdd=3.3, vss=0.0),
        },
        vdd=3.3,
        vss=0.0,
        tran_step="0.5n",
    )
    bridge = MixedSignalBridge(
        dut, [block], max_sync_interval_ns=5.0, simulator_lib=LIBNGSPICE,
    )

    dut.in_pin.value = 0
    sim_task = cocotb.start_soon(bridge.start(duration_ns=200.0))

    await Timer(40, unit="ns")
    dut._log.info("in_pin=0 -> out_pin=%s", dut.out_pin.value)
    assert int(dut.out_pin.value) == 0, "two inversions of 0 must read back 0"

    dut.in_pin.value = 1
    await Timer(80, unit="ns")
    dut._log.info("in_pin=1 -> out_pin=%s", dut.out_pin.value)
    assert int(dut.out_pin.value) == 1, "two inversions of 1 must read back 1"

    dut.in_pin.value = 0
    await Timer(60, unit="ns")
    dut._log.info("in_pin=0 -> out_pin=%s", dut.out_pin.value)
    assert int(dut.out_pin.value) == 0, "two inversions of 0 must read back 0 again"

    await sim_task
    await bridge.stop()
