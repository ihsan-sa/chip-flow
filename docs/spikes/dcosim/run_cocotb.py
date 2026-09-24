from pathlib import Path

from cocotb_tools.runner import get_runner

proj = Path(__file__).resolve().parent
runner = get_runner("icarus")
runner.build(
    sources=[proj / "two_inv_stub.v"],
    hdl_toplevel="two_inv_top",
    build_dir=proj / "sim_build",
)
runner.test(hdl_toplevel="two_inv_top", test_module="test_two_inv", test_dir=proj)
