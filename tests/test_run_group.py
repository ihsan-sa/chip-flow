"""engine/lib/checklib.py's run_group(): a tool's whole process tree dies
with the call - on timeout, on exit, and when the runner itself is killed
mid-sim. The "sim" is a sleeping python started through bin/eda, so it
runs under the image's ld-linux loader exactly as mcy's vvp sims do, and
carries a unique tag in its argv that /proc is searched for afterwards."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine"
EDA = str(REPO / "bin" / "eda")
sys.path.insert(0, str(ENGINE / "lib"))

import checklib  # noqa: E402


def alive(tag: str) -> list[int]:
    """pids whose argv carries tag (zombies have an empty cmdline)."""
    pids = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            argv = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if tag.encode() in argv:
            pids.append(int(d.name))
    return pids


def wait_for(pred, secs: float) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return pred()


def sim_tree(tag: str) -> list[str]:
    """A tool that forks two sims, one of them backgrounded, as mcy does."""
    sim = f'"{EDA}" python3 -c "import time; time.sleep(600)" {tag}'
    return ["/bin/sh", "-c", f"{sim} & {sim} & wait"]


def runner(tag: str, grouped: bool) -> subprocess.Popen:
    """A runner process (bin/eda python, like pytest itself) that starts
    sim_tree(tag) through run_group(), or through plain subprocess.run
    when not grouped. Its own session, so cleanup can reach everything."""
    call = ("checklib.run_group" if grouped else "subprocess.run")
    code = (f"import subprocess, sys; sys.path.insert(0, {str(ENGINE / 'lib')!r});"
            f" import checklib; {call}({sim_tree(tag)!r})")
    return subprocess.Popen([EDA, "python", "-c", code],
                            start_new_session=True)


def kill_runner_mid_sim(grouped: bool, grace: float) -> list[int]:
    tag = f"simtag-{uuid.uuid4().hex}"
    run = runner(tag, grouped)
    try:
        assert wait_for(lambda: len(alive(tag)) == 2, 60), "sims never started"
        run.send_signal(signal.SIGKILL)
        run.wait()
        wait_for(lambda: not alive(tag), grace)
        return alive(tag)
    finally:
        for pid in alive(tag):
            os.kill(pid, signal.SIGKILL)


def test_killed_runner_takes_its_sims_down():
    assert kill_runner_mid_sim(grouped=True, grace=10) == []


def test_plain_subprocess_run_leaves_sims_behind():
    # the case run_group() exists for: this is how nine sims ran two days
    assert len(kill_runner_mid_sim(grouped=False, grace=2)) == 2


def test_timeout_kills_every_sim():
    tag = f"simtag-{uuid.uuid4().hex}"
    with pytest.raises(subprocess.TimeoutExpired):
        checklib.run_group(sim_tree(tag), timeout=1)
    assert alive(tag) == []


def test_exit_kills_a_backgrounded_straggler():
    tag = f"simtag-{uuid.uuid4().hex}"
    sim = f'"{EDA}" python3 -c "import time; time.sleep(600)" {tag}'
    done = checklib.run_group(
        ["/bin/sh", "-c", f"{sim} & sleep 1; echo ran"],
        capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout == "ran\n"
    assert alive(tag) == []
