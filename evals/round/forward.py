#!/usr/bin/env python3
"""forward.py - the in-sandbox end of the eval round's network path.

    forward.py <port> <unix-socket> -- <command> [args...]

Runs INSIDE the bwrap sandbox (evals/round/sandbox.py), which has no network
of its own. It listens on 127.0.0.1:<port> (the sandbox's private loopback),
relays every accepted connection byte-for-byte to <unix-socket> (the host's
CONNECT proxy, evals/round/proxy.py, bound into the sandbox), then runs
<command> as a child and exits with the child's exit code. HTTPS_PROXY in
the sandbox points at 127.0.0.1:<port>, so every request the command makes
reaches the host proxy, which decides what is allowed; this script decides
nothing. It binds before starting the child, so the child never races an
unbound port. SIGTERM/SIGINT are passed on to the child.

Host python3 only, stdlib only: it runs before any toolchain is set up.
"""
from __future__ import annotations

import signal
import socket
import subprocess
import sys
import threading


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _serve(conn: socket.socket, sock_path: str) -> None:
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        up.connect(sock_path)
    except OSError:
        conn.close()
        up.close()
        return
    t = threading.Thread(target=_pump, args=(up, conn), daemon=True)
    t.start()
    _pump(conn, up)
    t.join()
    conn.close()
    up.close()


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[2] != "--":
        print("usage: forward.py <port> <unix-socket> -- <command> [args...]",
              file=sys.stderr)
        return 2
    port, sock_path, cmd = int(argv[0]), argv[1], argv[3:]
    if not cmd:
        print("forward.py: no command", file=sys.stderr)
        return 2
    lsn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsn.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsn.bind(("127.0.0.1", port))
    lsn.listen(64)

    def accept_loop() -> None:
        while True:
            try:
                conn, _ = lsn.accept()
            except OSError:
                return
            threading.Thread(target=_serve, args=(conn, sock_path),
                             daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    try:
        child = subprocess.Popen(cmd)
    except OSError as exc:
        print(f"forward.py: cannot start {cmd[0]}: {exc}", file=sys.stderr)
        return 127

    def relay(signum, _frame):
        try:
            child.send_signal(signum)
        except OSError:
            pass

    signal.signal(signal.SIGTERM, relay)
    signal.signal(signal.SIGINT, relay)
    rc = child.wait()
    return rc if rc >= 0 else 128 - rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
