"""Single-writer run lease for a project directory (PLAN §7, review round 3).

A lease is ``<project_root>/.run-lease`` created with ``O_EXCL``. It records the
owner's pid, process start time, hostname, and heartbeat. A second acquire fails
with ``LeaseHeldError``. A lease is reclaimed only when the owner is
*demonstrably dead*: the pid is not running, or the pid is running but its
process start time differs from the recorded one (pid reuse). A live owner with
a stale heartbeat or an exceeded wall-time budget is never reclaimed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from lib.state_io import atomic_write_json

LEASE_FILENAME = ".run-lease"


class LeaseHeldError(Exception):
    """Raised when the lease is held by another (possibly live) owner."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def process_start_time(pid: int) -> Optional[str]:
    """Return an opaque, stable start-time string for ``pid`` or None if unknown.

    Uses ``psutil`` when available, otherwise ``ps -o lstart=`` (macOS/Linux).
    """
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            return repr(psutil.Process(pid).create_time())
        except psutil.Error:
            return None
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    text = out.stdout.strip()
    return text or None


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def owner_is_dead(record: dict) -> bool:
    """True only if the recorded owner is demonstrably dead."""
    if record.get("hostname") != socket.gethostname():
        return False  # cannot judge a foreign host's process; treat as alive
    pid = record.get("pid")
    if not isinstance(pid, int):
        return False
    if not pid_is_running(pid):
        return True
    recorded_start = record.get("process_start_time")
    current_start = process_start_time(pid)
    if recorded_start and current_start and recorded_start != current_start:
        return True
    return False


class Lease:
    def __init__(self, path: Path, record: dict) -> None:
        self.path = path
        self.record = record
        self._released = False

    def heartbeat(self) -> None:
        if self._released:
            raise RuntimeError("lease already released")
        self.record["heartbeat_at"] = _now().isoformat()
        atomic_write_json(self.path, self.record)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if current.get("lease_id") == self.record["lease_id"]:
            self.path.unlink()

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def lease_path(project_root: Path | str) -> Path:
    return Path(project_root) / LEASE_FILENAME


def _try_create(path: Path, record: dict) -> bool:
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return True


def acquire(project_root: Path | str, wall_time_minutes: float) -> Lease:
    path = lease_path(project_root)
    now = _now()
    record = {
        "lease_id": os.urandom(8).hex(),
        "pid": os.getpid(),
        "process_start_time": process_start_time(os.getpid()),
        "hostname": socket.gethostname(),
        "acquired_at": now.isoformat(),
        "heartbeat_at": now.isoformat(),
        "wall_time_minutes": wall_time_minutes,
        "expires_at": (now + timedelta(minutes=wall_time_minutes)).isoformat(),
    }
    if _try_create(path, record):
        return Lease(path, record)

    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing = None
    except json.JSONDecodeError:
        existing = {}

    if existing is not None and not owner_is_dead(existing):
        raise LeaseHeldError(
            f"run lease {path} is held by a live owner "
            f"(pid {existing.get('pid')} on {existing.get('hostname')}, "
            f"heartbeat {existing.get('heartbeat_at')}); not reclaiming"
        )

    # Owner demonstrably dead (or lease vanished): reclaim once.
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    if _try_create(path, record):
        return Lease(path, record)
    raise LeaseHeldError(f"run lease {path} was re-acquired by another process during reclaim")
