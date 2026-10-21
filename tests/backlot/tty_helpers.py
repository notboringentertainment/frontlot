"""Hermetic real-process helpers shared by Backlot TTY tests."""

from __future__ import annotations

import json
import os
import select
import socket
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from backlot.tty import BYE, HELLO, IN, OUT, STATUS, decode_json_payload, encode_frame, encode_json_frame
from tests.lib.look_lock_helpers import PROJECT


REQUEST = "config-p-1"
CONFIG = {
    "version": "1.0",
    "budget_usd_cap": 5.0,
    "wall_time_minutes": 10,
    "cast_cap": {"characters": 1, "locations": 1},
    "default_video_endpoint": "test/reference-to-video",
    "provider_egress": {"provider": "fal", "content_classes": ["prompts"]},
}

_WRAPPER_BOOTSTRAP = r"""
import os
import sys
from scripts import gate_sign
gate_sign._gate_approve_argv = lambda project, request: [sys.executable, os.environ["OM_FAKE_SIGNER"]]
args = ["--project", os.environ["OM_PROJECT"], "--request", os.environ["OM_REQUEST"]]
if os.environ.get("OM_BROKER") == "1":
    args.append("--broker")
raise SystemExit(gate_sign.main(args))
"""

_FAKE_SIGNER = r"""
import fcntl
import os
import select
import signal
import struct
import sys
import termios
import time
import tty

def unexpected_signal(signum, _frame):
    os.write(1, ("UNEXPECTED_SIGNAL:" + str(signum) + "\n").encode("ascii"))
    raise SystemExit(95)

for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM):
    signal.signal(signum, unexpected_signal)

if not os.isatty(0) or not os.isatty(1):
    print("TTY_FAIL", flush=True)
    raise SystemExit(90)
root = os.environ.get("OM_PROJECT_ROOT")
if os.environ.get("OM_CHECK_CONTEXTS") == "1":
    if not root or not os.path.isfile(os.path.join(root, ".run-lease")):
        print("LEASE_MISSING", flush=True)
        raise SystemExit(92)
    lock = os.path.join(os.environ["OPENMONTAGE_GATES_DIR"], "sessions", os.environ["OM_PROJECT"] + ".lock")
    fd = os.open(lock, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            print("FLOCK_NOT_HELD", flush=True)
            raise SystemExit(93)
    finally:
        os.close(fd)
tty.setraw(0)
print("TTY_OK PARENT:" + str(os.getppid()), flush=True)
if os.environ.get("OM_HOSTILE_SPLIT") == "1":
    os.write(1, b"SPLIT_BEFORE\x1b]52;c;HOSTILE")
    expected_cols = int(os.environ.get("OM_EXPECT_COLS", "10"))
    expected_rows = int(os.environ.get("OM_EXPECT_ROWS", "3"))
    resize_deadline = time.monotonic() + 4.0
    observed = None
    while time.monotonic() < resize_deadline:
        rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8))
        if (cols, rows) == (expected_cols, expected_rows):
            observed = (cols, rows)
            break
        time.sleep(0.005)
    if observed is None:
        print("RESIZE_NOT_OBSERVED", flush=True)
        raise SystemExit(96)
    os.write(1, b"\x1b\\SPLIT_AFTER RESIZE_OBSERVED:%dx%d\n" % observed)
ready, _, _ = select.select([0], [], [], float(os.environ.get("OM_SIGNER_TIMEOUT", "5")))
if not ready:
    print("NO_HUMAN_BYTE", flush=True)
    raise SystemExit(94)
value = os.read(0, 1)
if value != os.environ.get("OM_HUMAN_BYTE", "y").encode("ascii"):
    print("UNEXPECTED_BYTE:" + value.hex(), flush=True)
    raise SystemExit(91)
print("HUMAN_BYTE:" + value.decode("ascii"), flush=True)
raise SystemExit(int(os.environ.get("OM_SIGNER_EXIT", "0")))
"""

_SELF_EXPIRING_SITECUSTOMIZE = r"""
import atexit
import os
import sys
import threading

if os.path.basename(sys.argv[0]) == "gate_approve.py":
    stopped = threading.Event()
    def expire():
        if not stopped.wait(float(os.environ["OM_TEST_SIGNER_SELF_EXPIRE"])):
            os._exit(97)
    threading.Thread(target=expire, name="test-signer-self-expiry", daemon=True).start()
    atexit.register(stopped.set)
"""


class DisposableResources:
    """Own exact disposable children and PTY descriptors without broad cleanup.

    Test children are constructed to expire on their own.  Teardown only polls
    and reaps those exact Popen objects; it never sends a signal or writes a
    byte to a signer's PTY as a cleanup shortcut.
    """

    def __init__(self) -> None:
        self._processes: dict[subprocess.Popen[Any], tuple[float, bool, str]] = {}
        self._fds: set[int] = set()

    def track_process(
        self,
        process: subprocess.Popen[Any],
        *,
        expires_after: float = 10.0,
        wrapper_last_resort: bool = False,
        label: str = "disposable child",
    ) -> subprocess.Popen[Any]:
        self._processes[process] = (time.monotonic() + expires_after, wrapper_last_resort, label)
        return process

    def reap(self, process: subprocess.Popen[Any], *, timeout: float = 10.0) -> bytes | str | None:
        output = process.communicate(timeout=timeout)[0]
        self._processes.pop(process, None)
        return output

    def track_fd(self, descriptor: int) -> int:
        self._fds.add(descriptor)
        return descriptor

    def close_fd(self, descriptor: int) -> None:
        if descriptor < 0:
            return
        try:
            os.close(descriptor)
        except OSError:
            pass
        self._fds.discard(descriptor)

    def cleanup(self) -> None:
        failures: list[str] = []
        try:
            for process, (deadline, wrapper_last_resort, label) in list(self._processes.items()):
                while process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                if process.poll() is None and wrapper_last_resort:
                    # Only the exact Popen for a disposable test wrapper is
                    # touched, and only after its signer self-expiry window.
                    # PID-directed signals never target the separately spawned
                    # signer, and no terminal byte is written for cleanup.
                    try:
                        process.terminate()
                    except OSError as exc:
                        failures.append(f"{label} pid {process.pid} terminate failed: {exc}")
                    if process.poll() is None:
                        try:
                            process.wait(timeout=0.25)
                        except subprocess.TimeoutExpired:
                            try:
                                process.kill()
                            except OSError as exc:
                                failures.append(f"{label} pid {process.pid} kill failed: {exc}")
                            if process.poll() is None:
                                try:
                                    process.wait(timeout=2.0)
                                except subprocess.TimeoutExpired:
                                    failures.append(f"{label} pid {process.pid} resisted exact-wrapper cleanup")
                    failures.append(f"{label} pid {process.pid} required exact-wrapper last-resort cleanup")
                elif process.poll() is None:
                    failures.append(f"{label} pid {process.pid} did not self-expire and was not wrapper-owned")

                if process.poll() is None:
                    continue
                try:
                    process.communicate(timeout=0)
                except (subprocess.TimeoutExpired, ValueError):
                    pass
                self._processes.pop(process, None)
        finally:
            # Descriptors are always closed, including when process cleanup or
            # failure reporting raises. Wrapper fallbacks run only after the
            # owned fake/real signer has had its bounded self-expiry window.
            for descriptor in list(self._fds):
                self.close_fd(descriptor)
        if failures:
            raise AssertionError("; ".join(failures))


def wait_until(predicate, *, timeout: float = 3.0, description: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {description}")


def write_project(projects: Path, *, request_id: str = REQUEST) -> tuple[Path, dict[str, Any]]:
    root = projects / PROJECT
    (root / ".gate-requests").mkdir(parents=True)
    raw = yaml.safe_dump(CONFIG, sort_keys=True).encode("utf-8")
    (root / "project.yaml").write_bytes(raw)
    (root / "project.json").write_text(
        json.dumps({
            "project_id": PROJECT,
            "title": PROJECT,
            "pipeline_type": "authored-film",
            "created_at": "2026-08-30T00:00:00Z",
        }),
        encoding="utf-8",
    )
    import hashlib

    request = {
        "request_id": request_id,
        "project_id": PROJECT,
        "stage": "config",
        "scope": "project:config",
        "kind": "config",
        "entity_id": "project-config",
        "artifact": None,
        "approval_record": {"config_sha256": hashlib.sha256(raw).hexdigest()},
        "source_checkpoint_digest": None,
        "summary": "Review PROJECT configuration.",
        "preview_paths": [],
    }
    (root / ".gate-requests" / f"{request_id}.json").write_text(json.dumps(request), encoding="utf-8")
    return root, request


def fake_signer(tmp_path: Path) -> Path:
    path = tmp_path / "fake_signer.py"
    path.write_text(_FAKE_SIGNER, encoding="utf-8")
    return path


def process_env(projects: Path, gates: Path, signer: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "OPENMONTAGE_PROJECTS_DIR": str(projects),
        "OPENMONTAGE_GATES_DIR": str(gates),
        "OM_PROJECT": PROJECT,
        "OM_REQUEST": REQUEST,
        "PYTHONUNBUFFERED": "1",
    })
    if signer is not None:
        env["OM_FAKE_SIGNER"] = str(signer)
    return env


def add_real_signer_self_expiry(tmp_path: Path, env: dict[str, str], *, timeout: float) -> dict[str, str]:
    """Bound a real gate_approve test child without signals or synthetic input."""
    hook_dir = tmp_path / "self-expiring-python"
    hook_dir.mkdir()
    (hook_dir / "sitecustomize.py").write_text(_SELF_EXPIRING_SITECUSTOMIZE, encoding="utf-8")
    bounded = dict(env)
    existing = bounded.get("PYTHONPATH")
    bounded["PYTHONPATH"] = str(hook_dir) if not existing else str(hook_dir) + os.pathsep + existing
    bounded["OM_TEST_SIGNER_SELF_EXPIRE"] = str(timeout)
    return bounded


def launch_fake_broker(
    projects: Path,
    gates: Path,
    signer: Path,
    *,
    owner: DisposableResources | None = None,
    wait_ready: bool = True,
    **env_overrides: str,
) -> subprocess.Popen[bytes]:
    env = process_env(projects, gates, signer)
    env.update({"OM_BROKER": "1", **env_overrides})
    process = subprocess.Popen(
        [sys.executable, "-c", _WRAPPER_BOOTSTRAP],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if owner is not None:
        signer_timeout = float(env.get("OM_SIGNER_TIMEOUT", "5"))
        owner.track_process(
            process,
            expires_after=signer_timeout + 5.0,
            wrapper_last_resort=True,
            label="fake broker wrapper",
        )
    if wait_ready:
        wait_for_socket(gates / "sessions" / f"{PROJECT}.sock", process)
    return process


def wait_for_socket(path: Path, process: subprocess.Popen[bytes], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    sidecar = path.with_suffix(".session.json")
    while time.monotonic() < deadline:
        try:
            published = json.loads(sidecar.read_text(encoding="utf-8"))
            if stat.S_ISSOCK(path.lstat().st_mode) and published.get("pid") == process.pid:
                return
        except (FileNotFoundError, json.JSONDecodeError, AttributeError):
            pass
        if process.poll() is not None:
            output = process.stdout.read().decode(errors="replace") if process.stdout else ""
            raise AssertionError(f"broker exited {process.returncode} before socket publication: {output}")
        time.sleep(0.01)
    raise AssertionError(f"broker socket was not published at {path}")


def recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError(f"socket closed with {remaining} bytes outstanding")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_frame(sock: socket.socket) -> tuple[int, bytes]:
    header = recv_exact(sock, 5)
    frame_type, length = struct.unpack("!BI", header)
    return frame_type, recv_exact(sock, length)


def connect(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(3.0)
    sock.connect(str(path))
    return sock


def attach(path: Path, *, project: str = PROJECT, request_id: str = REQUEST) -> tuple[socket.socket, dict[str, Any]]:
    sock = connect(path)
    frame_type, payload = recv_frame(sock)
    assert frame_type == HELLO
    hello = decode_json_payload(HELLO, payload)
    sock.sendall(encode_json_frame(HELLO, {"project": project, "request_id": request_id, "token_ok": True}))
    return sock, hello


def attach_when_free(
    path: Path,
    *,
    project: str = PROJECT,
    request_id: str = REQUEST,
    timeout: float = 3.0,
) -> tuple[socket.socket, dict[str, Any]]:
    """Poll the protocol condition itself until the broker accepts a client."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock = connect(path)
        frame_type, payload = recv_frame(sock)
        if frame_type == HELLO:
            hello = decode_json_payload(HELLO, payload)
            sock.sendall(encode_json_frame(HELLO, {
                "project": project,
                "request_id": request_id,
                "token_ok": True,
            }))
            return sock, hello
        sock.close()
        if frame_type != BYE or decode_json_payload(BYE, payload).get("reason") != "busy":
            raise AssertionError("broker refused polling client for an unexpected reason")
        time.sleep(0.01)
    raise AssertionError("timed out waiting for broker to become attachable")


def collect_until_bye(sock: socket.socket) -> tuple[list[bytes], list[dict[str, Any]]]:
    output: list[bytes] = []
    events: list[dict[str, Any]] = []
    while True:
        frame_type, payload = recv_frame(sock)
        if frame_type == OUT:
            output.append(payload)
        elif frame_type in (STATUS, BYE):
            event = decode_json_payload(frame_type, payload)
            events.append({"frame_type": frame_type, **event})
            if frame_type == BYE:
                return output, events
        else:
            raise AssertionError(f"unexpected frame type {frame_type}")


def wait_for_terminal_output(sock: socket.socket, marker: bytes = b"TTY_OK") -> bytes:
    output = bytearray()
    while marker not in output:
        frame_type, payload = recv_frame(sock)
        assert frame_type == OUT
        output.extend(payload)
    return bytes(output)


def finish_broker(path: Path, process: subprocess.Popen[bytes], sock: socket.socket | None = None) -> bytes:
    active = sock
    try:
        if active is None:
            active, _ = attach(path)
        wait_for_terminal_output(active)
        active.sendall(encode_frame(IN, b"y"))
        collect_until_bye(active)
    finally:
        if active is not None:
            active.close()
    output = process.communicate(timeout=8)[0]
    assert process.returncode == 0, output.decode(errors="replace")
    return output


def read_pty_until_exit(process: subprocess.Popen[bytes], master: int, timeout: float = 10.0) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                chunk = b""
            if chunk:
                output.extend(chunk)
        if process.poll() is not None:
            while True:
                ready, _, _ = select.select([master], [], [], 0)
                if not ready:
                    return bytes(output)
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    return bytes(output)
                if not chunk:
                    return bytes(output)
                output.extend(chunk)
    raise AssertionError(f"PTY process did not exit; output={bytes(output)!r}")


def read_pty_until_marker(
    process: subprocess.Popen[bytes],
    master: int,
    marker: bytes,
    *,
    timeout: float = 10.0,
) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + timeout
    while marker not in output and time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(
                f"PTY process exited {process.returncode} before marker {marker!r}; output={bytes(output)!r}"
            )
        ready, _, _ = select.select([master], [], [], min(0.05, max(0.0, deadline - time.monotonic())))
        if ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            output.extend(chunk)
    if marker not in output:
        raise AssertionError(f"PTY marker {marker!r} was not observed; output={bytes(output)!r}")
    return bytes(output)
