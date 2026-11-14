import json
import os
import socket
import subprocess
import sys
import threading

import pytest

from lib import run_lease
from lib.run_lease import LeaseHeldError


def test_acquire_creates_lease_with_identity(tmp_path):
    with run_lease.acquire(tmp_path, 30) as lease:
        data = json.loads((tmp_path / ".run-lease").read_text())
        assert data["pid"] == os.getpid()
        assert data["hostname"] == socket.gethostname()
        assert set(data) >= {"pid", "process_start_time", "hostname", "heartbeat_at", "acquired_at"}
        before = data["heartbeat_at"]
        lease.heartbeat()
        after = json.loads((tmp_path / ".run-lease").read_text())["heartbeat_at"]
        assert after >= before
    assert not (tmp_path / ".run-lease").exists()


def test_second_acquire_fails_while_held(tmp_path):
    with run_lease.acquire(tmp_path, 30):
        with pytest.raises(LeaseHeldError, match="live owner"):
            run_lease.acquire(tmp_path, 30)


def test_live_owner_with_stale_heartbeat_is_not_reclaimed(tmp_path):
    with run_lease.acquire(tmp_path, 1) as lease:
        lease.record["heartbeat_at"] = "2000-01-01T00:00:00+00:00"
        lease.record["expires_at"] = "2000-01-01T00:01:00+00:00"
        run_lease.atomic_write_json(lease.path, lease.record)
        with pytest.raises(LeaseHeldError, match="live owner"):
            run_lease.acquire(tmp_path, 1)


def _write_lease(tmp_path, **overrides):
    record = {
        "lease_id": "dead",
        "pid": os.getpid(),
        "process_start_time": run_lease.process_start_time(os.getpid()),
        "hostname": socket.gethostname(),
        "acquired_at": "2000-01-01T00:00:00+00:00",
        "heartbeat_at": "2000-01-01T00:00:00+00:00",
    }
    record.update(overrides)
    (tmp_path / ".run-lease").write_text(json.dumps(record))


def test_reclaims_when_pid_not_running(tmp_path, monkeypatch):
    _write_lease(tmp_path)

    def dead(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(run_lease.os, "kill", dead)
    with run_lease.acquire(tmp_path, 30) as lease:
        assert lease.record["lease_id"] != "dead"


def test_reclaims_when_pid_reused_with_different_start_time(tmp_path, monkeypatch):
    _write_lease(tmp_path, process_start_time="Mon Jan  1 00:00:00 2000")
    monkeypatch.setattr(run_lease, "process_start_time", lambda pid: "Tue Jan  2 00:00:00 2001")
    with run_lease.acquire(tmp_path, 30) as lease:
        assert lease.record["lease_id"] != "dead"


def test_live_pid_with_same_start_time_not_reclaimed(tmp_path, monkeypatch):
    _write_lease(tmp_path, process_start_time="same")
    monkeypatch.setattr(run_lease, "process_start_time", lambda pid: "same")
    with pytest.raises(LeaseHeldError, match="live owner"):
        run_lease.acquire(tmp_path, 30)


def test_foreign_host_lease_is_never_reclaimed(tmp_path, monkeypatch):
    _write_lease(tmp_path, hostname="another-machine.local", pid=1)
    with pytest.raises(LeaseHeldError):
        run_lease.acquire(tmp_path, 30)


def test_competing_stale_reclaimers_cannot_replace_the_new_live_lease(tmp_path, monkeypatch):
    """A late stale observer must not unlink the lease a winner just created."""
    _write_lease(tmp_path, pid=999_999_999)
    original_unlink = run_lease.Path.unlink
    original_try_create = run_lease._try_create
    late_at_unlink = threading.Event()
    early_created = threading.Event()

    def controlled_unlink(path, *args, **kwargs):
        if path == tmp_path / ".run-lease" and threading.current_thread().name == "late-reclaimer":
            late_at_unlink.set()
            early_created.wait(timeout=0.35)
        return original_unlink(path, *args, **kwargs)

    def observed_try_create(path, record):
        created = original_try_create(path, record)
        if created and threading.current_thread().name == "early-reclaimer":
            early_created.set()
        return created

    monkeypatch.setattr(run_lease.Path, "unlink", controlled_unlink)
    monkeypatch.setattr(run_lease, "_try_create", observed_try_create)
    leases = []
    errors = []

    def claim():
        try:
            leases.append(run_lease.acquire(tmp_path, 30))
        except LeaseHeldError as exc:
            errors.append(exc)

    late = threading.Thread(target=claim, name="late-reclaimer")
    early = threading.Thread(target=claim, name="early-reclaimer")
    late.start()
    assert late_at_unlink.wait(timeout=1)
    early.start()
    late.join(timeout=2)
    early.join(timeout=2)
    assert not late.is_alive() and not early.is_alive()
    try:
        current = json.loads((tmp_path / ".run-lease").read_text(encoding="utf-8"))
        assert len(leases) == 1
        assert len(errors) == 1
        assert current["lease_id"] == leases[0].record["lease_id"]
    finally:
        for lease in leases:
            lease.release()


def test_process_start_time_psutil_branch(monkeypatch):
    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return 1234.5

    class FakePsutil:
        Error = RuntimeError
        Process = FakeProc

    monkeypatch.setitem(sys.modules, "psutil", FakePsutil)
    assert run_lease.process_start_time(42) == repr(1234.5)


def test_process_start_time_ps_branch(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)  # makes `import psutil` raise ImportError
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="Mon Jan  1 00:00:00 2024\n", stderr="")

    monkeypatch.setattr(run_lease.subprocess, "run", fake_run)
    assert run_lease.process_start_time(42) == "Mon Jan  1 00:00:00 2024"
    assert calls[0][:3] == ["ps", "-o", "lstart="]

    monkeypatch.setattr(
        run_lease.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""),
    )
    assert run_lease.process_start_time(42) is None


def test_process_start_time_real_for_self():
    assert run_lease.process_start_time(os.getpid())
