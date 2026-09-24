"""safelib.py - pre-credential order/state safety primitives.

Ported verbatim in mechanism from /hwde's scripts/lib/safelib.py (docs/
design.md 1.3): the file-safety layer under state.py has nothing PCB-specific
in it, so the port is the file as-is, domain-free. Pure stdlib, Windows +
POSIX. Used by state.py (writer lock + base-digest compare-and-swap +
contained snapshot/restore) and anything else that mutates a workspace file.

  writer_lock(path)        OS-exclusive advisory lock on <path>.lock; process-
                           wide re-entrant per thread, blocks other threads and
                           other processes; bounded wait -> LockBusy.
  atomic_write_*(path, x)  unique temp in the SAME dir (mkstemp) -> fsync ->
                           os.replace -> dir fsync. Two writers never share a
                           temp name; a crash leaves old-or-new, never a torn
                           file.
  append_journal(path, r)  O_APPEND one-JSON-line-per-record + fsync. Never
                           truncates; corrupt lines are reported, not repaired.
  contained_rel(root, rel) rejects absolute paths, drive letters, '..'/'.'
                           components, empty/NUL names, backslash traversal,
                           and any symlink component under root; the target
                           must resolve inside root.
  FAULT_HOOK               fault-injection point for the test suite: a
                           callable(point: str, **ctx) or None. Production
                           never sets it.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath

from checklib import CheckError

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

DEFAULT_LOCK_TIMEOUT = 30.0
_LOCK_POLL = 0.05

FAULT_HOOK = None  # tests only: callable(point, **ctx) -> None


def fault(point: str, **ctx) -> None:
    hook = FAULT_HOOK
    if hook is not None:
        hook(point, **ctx)


def now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ----------------------------------------------------------------- errors

class LockBusy(CheckError):
    """The writer lock is held elsewhere and the bounded wait expired."""


class StaleWriteError(CheckError):
    """Compare-and-swap failed: the file changed since it was loaded."""


class ContainmentError(CheckError):
    """A snapshot/restore entry would escape the workspace."""


class JournalCorrupt(CheckError):
    """An attempt-journal line is unreadable. Never auto-repaired."""


# ------------------------------------------------------------------ digests

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------- locks

class _Entry:
    __slots__ = ("rlock", "depth", "fd")

    def __init__(self):
        self.rlock = threading.RLock()
        self.depth = 0
        self.fd = None


_REGISTRY: dict[str, _Entry] = {}
_REGISTRY_GUARD = threading.Lock()


def lock_path_for(target: Path | str) -> Path:
    target = Path(target)
    return target.with_name(target.name + ".lock")


def _os_try_lock(fd: int) -> bool:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except (BlockingIOError, PermissionError, OSError):
        return False


def _os_unlock(fd: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        else:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def _holder_note(lock_path: Path) -> str:
    try:
        txt = lock_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return f" (holder: {txt[:120]})" if txt else ""


@contextmanager
def writer_lock(target: Path | str, timeout: float | None = None,
                what: str = "file"):
    """Exclusive writer lock for `target` (held on `<target>.lock`).

    Re-entrant for the same thread (nested load->save inside a CLI-level
    hold), exclusive across threads and processes. Waits up to `timeout`
    seconds (default DEFAULT_LOCK_TIMEOUT) then raises LockBusy. The lock
    file persists (never deleted: deleting a lock file races other waiters).
    """
    timeout = DEFAULT_LOCK_TIMEOUT if timeout is None else float(timeout)
    lock_path = lock_path_for(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path.resolve())
    with _REGISTRY_GUARD:
        entry = _REGISTRY.setdefault(key, _Entry())
    if not entry.rlock.acquire(timeout=max(timeout, 0.0)):
        raise LockBusy(
            f"{what} writer lock {lock_path} is held by another thread of "
            f"this process (waited {timeout:g}s)")
    acquired_os = False
    try:
        if entry.depth == 0:
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
            deadline = time.monotonic() + timeout
            try:
                while not _os_try_lock(fd):
                    if time.monotonic() >= deadline:
                        raise LockBusy(
                            f"{what} writer lock {lock_path} is held by "
                            f"another process{_holder_note(lock_path)} - "
                            f"waited {timeout:g}s; refusing rather than "
                            "proceeding unlocked")
                    time.sleep(_LOCK_POLL)
            except BaseException:
                os.close(fd)
                raise
            if fcntl is not None:  # informative only; Windows locks byte 0
                try:
                    os.ftruncate(fd, 0)
                    os.write(fd, f"pid {os.getpid()} {now_iso()}".encode())
                except OSError:
                    pass
            entry.fd = fd
            acquired_os = True
        entry.depth += 1
        try:
            yield lock_path
        finally:
            entry.depth -= 1
            if entry.depth == 0 and entry.fd is not None:
                _os_unlock(entry.fd)
                os.close(entry.fd)
                entry.fd = None
    finally:
        entry.rlock.release()
        if acquired_os and entry.depth != 0:
            # a nested generator was abandoned mid-yield; never leave the OS
            # lock dangling in-process
            pass


# ----------------------------------------------------------- atomic writes

def _fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _target_mode(path: Path) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return 0o644


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Unique temp (mkstemp, same dir) -> write -> fsync -> os.replace ->
    dir fsync. Concurrent writers of the same target never share a temp
    name; a crash at any point leaves the old file or the new one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = _target_mode(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                               dir=str(path.parent))
    tmp_p = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        fault("atomic_write.before_replace", path=path, tmp=tmp_p)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp_p.unlink()
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def atomic_write_text(path: Path | str, text: str,
                      encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path | str, doc, indent: int = 1) -> str:
    text = json.dumps(doc, indent=indent)
    atomic_write_bytes(path, text.encode("utf-8"))
    return text


# ----------------------------------------------------------------- journal

def append_journal(path: Path | str, record: dict) -> dict:
    """Append one JSON line (ts + pid stamped) with O_APPEND and fsync.
    The journal is append-only: nothing here ever rewrites or truncates it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": now_iso(), "pid": os.getpid(), **record}
    line = json.dumps(rec, sort_keys=True, ensure_ascii=True) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return rec


def read_journal(path: Path | str) -> list[dict]:
    """All records, in order. A corrupt line raises JournalCorrupt (the
    journal is evidence; it is reported, never repaired)."""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JournalCorrupt(
                    f"{path}:{n} is not a JSON record ({exc}) - the attempt "
                    "journal is append-only evidence; inspect it by hand") \
                    from exc
            if not isinstance(rec, dict):
                raise JournalCorrupt(f"{path}:{n} is not a JSON object")
            out.append(rec)
    return out


# ------------------------------------------------------------- containment

def contained_rel(root: Path | str, rel, what: str = "path") -> Path:
    """Return root/rel after proving rel cannot escape root.

    Rejects: non-string/empty/NUL names, absolute POSIX or Windows paths
    (incl. drive letters and UNC), '..' or '.' components, empty components
    ('a//b'), and any component under root that is a symlink (existing
    ones only - a missing tail is allowed for restore targets). The final
    path must resolve inside the resolved root."""
    root = Path(root)
    if not isinstance(rel, str) or not rel or not rel.strip():
        raise ContainmentError(f"{what}: empty or non-string entry {rel!r}")
    if "\x00" in rel:
        raise ContainmentError(f"{what}: NUL byte in entry {rel!r}")
    norm = rel.replace("\\", "/")
    pp = PurePosixPath(norm)
    pw = PureWindowsPath(rel)
    if pp.is_absolute() or pw.is_absolute() or pw.drive or pw.root \
            or norm.startswith("/") or norm.startswith("//"):
        raise ContainmentError(f"{what}: absolute path refused: {rel!r}")
    parts = norm.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ContainmentError(
            f"{what}: traversal or empty component refused: {rel!r}")
    cur = root
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            raise ContainmentError(
                f"{what}: symlink component refused: {cur} (entry {rel!r})")
    try:
        root_r = root.resolve()
        tgt_r = cur.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ContainmentError(f"{what}: cannot resolve {rel!r}: {exc}") \
            from exc
    if tgt_r != root_r and root_r not in tgt_r.parents:
        raise ContainmentError(
            f"{what}: {rel!r} resolves outside {root} ({tgt_r})")
    return cur


def stage_copy(src: Path, target: Path) -> Path:
    """Copy src to a unique temp beside target (fsync'd). The caller
    verifies the staged bytes and then os.replace()s it into place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.restore-",
                               suffix=".tmp", dir=str(target.parent))
    tmp_p = Path(tmp)
    try:
        with open(src, "rb") as sf, os.fdopen(fd, "wb") as df:
            for chunk in iter(lambda: sf.read(1 << 20), b""):
                df.write(chunk)
            df.flush()
            os.fsync(df.fileno())
        try:
            os.chmod(tmp, _target_mode(src))
        except OSError:
            pass
    except BaseException:
        try:
            tmp_p.unlink()
        except OSError:
            pass
        raise
    return tmp_p


def sweep_stale_stage_temps(target: Path) -> int:
    """Remove leftover `.<name>.restore-*.tmp` beside target (a crashed
    earlier restore). They are ours by construction; nothing else makes
    that name. Returns the count removed."""
    n = 0
    parent = target.parent
    if not parent.is_dir():
        return 0
    for p in parent.glob(f".{target.name}.restore-*.tmp"):
        if p.is_symlink() or not p.is_file():
            continue
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
