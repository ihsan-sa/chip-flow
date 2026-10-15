#!/usr/bin/env python
"""proxy.py - the host-side CONNECT proxy for one sandboxed eval run.

    proxy.py --socket PATH --log PATH [--allow HOST:PORT ...]   (serve until killed)

The run's sandbox (evals/round/sandbox.py) has no network namespace of its
own, so this proxy is its only way out. It listens on a unix socket that is
bound into the sandbox; the in-sandbox forwarder (forward.py) relays
127.0.0.1:<port> to it, and HTTPS_PROXY points there.

What it allows, and nothing else:
  * only the CONNECT method (a tunnel; the proxy never sees inside TLS);
  * only to an exact host:port in the allowlist, matched case-insensitively
    on the name the client asked for - no wildcards, no IP literals unless
    listed. The default allowlist is DEFAULT_ALLOW: the Messages API host
    and the OAuth token endpoint's host (a long run must be able to refresh
    its access token, and the refresh has to land in the real credentials
    file - see sandbox.py).
The socket is bound through a /proc/self/fd path to its directory, so a
run dir deeper than the 107-byte unix socket limit still works.
Every request, allowed or denied, is one JSON line in the log:
{"ts", "method", "target", "allowed", "reason"}. A request header over
MAX_HEADER bytes, or not complete within HEADER_TIMEOUT_S, is refused.
An allowed tunnel whose upstream connect fails is logged with the error.

Limits: it filters by host name only, because it never terminates TLS.
Every path the allowed hosts serve is reachable through it with whatever
the sandbox's credentials permit (round.py's docstring lists what that
leaves open).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

DEFAULT_ALLOW = ("api.anthropic.com:443", "platform.claude.com:443")
MAX_HEADER = 16384
HEADER_TIMEOUT_S = 20.0
UPSTREAM_TIMEOUT_S = 20.0


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ConnectProxy:
    """A threaded CONNECT proxy on a unix socket. start() returns once it is
    listening; stop() closes the listener (open tunnels end with their
    peers)."""

    def __init__(self, sock_path: Path, log_path: Path,
                 allow: tuple[str, ...] = DEFAULT_ALLOW,
                 connect=None):
        self.sock_path = Path(sock_path)
        self.log_path = Path(log_path)
        self.allow = {a.lower() for a in allow}
        self._connect = connect or (lambda host, port: socket.create_connection(
            (host, port), timeout=UPSTREAM_TIMEOUT_S))
        self._lsn: socket.socket | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # -- logging ---------------------------------------------------------
    def _log(self, **rec) -> None:
        rec = {"ts": _now(), **rec}
        line = json.dumps(rec, sort_keys=True)
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # -- lifecycle -------------------------------------------------------
    def start(self) -> "ConnectProxy":
        self.sock_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.sock_path.exists() or self.sock_path.is_symlink():
            self.sock_path.unlink()
        lsn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # A unix socket path is limited to 107 bytes and a run dir can be
        # deeper than that, so bind through a /proc/self/fd path to the
        # directory: the socket lands at sock_path all the same.
        dfd = os.open(self.sock_path.parent, os.O_PATH | os.O_DIRECTORY)
        try:
            lsn.bind(f"/proc/self/fd/{dfd}/{self.sock_path.name}")
        finally:
            os.close(dfd)
        os.chmod(self.sock_path, 0o600)
        lsn.listen(64)
        self._lsn = lsn
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._lsn is not None:
            try:
                self._lsn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._lsn.close()
            self._lsn = None
        try:
            self.sock_path.unlink()
        except OSError:
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def _accept_loop(self) -> None:
        while self._lsn is not None:
            try:
                conn, _ = self._lsn.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    # -- one client ------------------------------------------------------
    def _read_header(self, conn: socket.socket) -> bytes | None:
        conn.settimeout(HEADER_TIMEOUT_S)
        buf = b""
        while b"\r\n\r\n" not in buf:
            if len(buf) > MAX_HEADER:
                return None
            chunk = conn.recv(4096)
            if not chunk:
                return None
            buf += chunk
        return buf

    def _handle(self, conn: socket.socket) -> None:
        try:
            try:
                head = self._read_header(conn)
            except OSError:
                head = None
            if head is None:
                self._log(method=None, target=None, allowed=False,
                          reason="bad or oversized request header")
                return
            head, _, rest = head.partition(b"\r\n\r\n")
            first = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            parts = first.split()
            method = parts[0].upper() if parts else ""
            target = parts[1] if len(parts) > 1 else ""
            if method != "CONNECT":
                self._log(method=method, target=target[:200], allowed=False,
                          reason="only CONNECT is proxied")
                conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n"
                             b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                return
            host, sep, port_s = target.rpartition(":")
            key = target.lower()
            if not sep or not port_s.isdigit() or key not in self.allow:
                self._log(method=method, target=target[:200], allowed=False,
                          reason="not in allowlist")
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\n"
                             b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                return
            try:
                up = self._connect(host, int(port_s))
            except OSError as exc:
                self._log(method=method, target=target, allowed=True,
                          reason=f"upstream connect failed: {exc}")
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n"
                             b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                return
            self._log(method=method, target=target, allowed=True,
                      reason="allowlisted")
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            conn.settimeout(None)
            up.settimeout(None)
            if rest:
                up.sendall(rest)
            t = threading.Thread(target=_pump, args=(up, conn), daemon=True)
            t.start()
            _pump(conn, up)
            t.join()
            up.close()
        finally:
            try:
                conn.close()
            except OSError:
                pass


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


def summarize_log(log_path: Path) -> dict:
    """Counts of allowed and denied targets in a proxy log."""
    allowed: dict[str, int] = {}
    denied: dict[str, int] = {}
    p = Path(log_path)
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            bucket = allowed if rec.get("allowed") else denied
            key = f"{rec.get('method')} {rec.get('target')}"
            bucket[key] = bucket.get(key, 0) + 1
    return {"allowed": allowed, "denied": denied}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--socket", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--allow", action="append")
    a = ap.parse_args(argv)
    try:
        px = ConnectProxy(Path(a.socket), Path(a.log),
                          tuple(a.allow) if a.allow else DEFAULT_ALLOW).start()
    except OSError as exc:
        print(json.dumps({"script": "proxy.py", "status": "error",
                          "error": str(exc),
                          "remediation": "check the socket path's directory "
                                         "exists and is writable"}))
        return 2
    print(json.dumps({"script": "proxy.py", "status": "listening",
                      "socket": a.socket, "allow": sorted(px.allow)}),
          flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        px.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
