#!/usr/bin/env python3
"""Hold the project lease and run the human gate signer in a real terminal.

With ``--broker`` this process owns a PTY and a private Unix socket so Backlot
servers may detach and reconnect without owning or signalling the signer.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import errno
import fcntl
import json
import os
import pty
import selectors
import signal
import socket
import struct
import subprocess
import sys
import termios
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional


REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backlot.ansi import AnsiSanitizer
from backlot.tty import (
    BYE,
    HELLO,
    IN,
    OUT,
    RESIZE,
    STATUS,
    FrameParser,
    ProtocolError,
    decode_json_payload,
    encode_frame,
    encode_json_frame,
    load_safe_pending_request,
    session_paths,
    valid_resize,
)
from lib import run_lease
from lib.run_common import RunError, resolve_project_root, validate_request_id


DEFAULT_LEASE_MINUTES = 60.0
REPLAY_BYTES = 64 * 1024
MAX_CLIENT_QUEUE = 1024 * 1024
_LIFECYCLE_SIGNALS = frozenset((signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT))


def _gate_approve_argv(project: str, request_id: str) -> list[str]:
    """Private child argv; public callers use ``run_common.gate_invocation``."""
    python = REPO / ".venv" / "bin" / "python"
    return [str(python), "scripts/gate_approve.py", "--project", project, "--request", request_id]


def _lease_minutes(root: Path) -> float:
    """Read the simple scalar without requiring a YAML package or approval."""
    try:
        for line in (root / "project.yaml").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() == "wall_time_minutes":
                parsed = float(value.split("#", 1)[0].strip())
                if 0 < parsed < 365 * 24 * 60:
                    return parsed
    except (OSError, ValueError, OverflowError):
        pass
    return DEFAULT_LEASE_MINUTES


def _lease_owner_pid(root: Path) -> Any:
    try:
        record = json.loads(run_lease.lease_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "?"
    return record.get("pid", "?") if isinstance(record, dict) else "?"


def _validate_pending(root: Path, project: str, request_id: str) -> dict[str, Any]:
    """Use the relay's neutral, inode-stable structural validator."""
    try:
        return load_safe_pending_request(root, project, request_id)
    except (FileNotFoundError, ValueError) as exc:
        raise RunError(str(exc)) from exc


@contextlib.contextmanager
def _project_flock(project: str) -> Iterator[None]:
    path = session_paths(project)["lock"]
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunError("a signing terminal is already open for this project") from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _login_environment() -> dict[str, str]:
    environment = os.environ.copy()
    shell = environment.get("SHELL") or "/bin/sh"
    try:
        result = subprocess.run(
            [shell, "-lc", 'printf %s "$PATH"'],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        login_path = result.stdout.strip()
        if result.returncode == 0 and login_path and "\x00" not in login_path:
            environment["PATH"] = login_path
    except (OSError, subprocess.SubprocessError):
        pass
    environment["TERM"] = environment.get("TERM") or "xterm-256color"
    return environment


def _write_sidecar(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OSError("unable to write session sidecar")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _log(project: str, request_id: str, message: str) -> None:
    """Append redacted lifecycle data to Backlot's private log."""
    try:
        log = session_paths(project)["socket"].parent.parent / "server.log"
        with log.open("a", encoding="utf-8") as handle:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            handle.write(f"{stamp} broker project={project} request={request_id} {message}\n")
    except OSError:
        pass


def _foreground(project: str, request_id: str, lifecycle: "_HoldLifecycleSignals") -> int:
    argv = _gate_approve_argv(project, request_id)
    child = lifecycle.spawn(argv, cwd=REPO)
    try:
        return child.wait()
    finally:
        _reap_exact_child(child)


def _reap_exact_child(child: subprocess.Popen) -> int:
    """Wait for this exact child without writing to or signalling it."""
    while child.poll() is None:
        try:
            child.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            continue
        except InterruptedError:
            continue
    return int(child.returncode)


class _HoldLifecycleSignals:
    """Defer wrapper termination until its signer child has been reaped."""

    def __init__(self) -> None:
        self.received: list[int] = []
        self.previous: dict[int, Any] = {}

    def __enter__(self) -> "_HoldLifecycleSignals":
        def remember(signum, _frame) -> None:
            self.received.append(signum)

        for signum in _LIFECYCLE_SIGNALS:
            self.previous[signum] = signal.getsignal(signum)
            signal.signal(signum, remember)
        return self

    def spawn(self, argv: list[str], **kwargs: Any) -> subprocess.Popen:
        """Atomically commit signer creation against lifecycle signals.

        The child restores the caller's original mask before exec. Signals
        arriving after the blocked pending check are therefore classified as
        post-commit and delivered only to this wrapper after Popen returns.
        """
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _LIFECYCLE_SIGNALS)

        def restore_child_mask() -> None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

        try:
            pending = signal.sigpending()
            if self.received or not _LIFECYCLE_SIGNALS.isdisjoint(pending):
                raise _DeferredLifecycleSignal
            if "preexec_fn" in kwargs:
                raise TypeError("signer spawn owns preexec_fn")
            return subprocess.Popen(argv, preexec_fn=restore_child_mask, **kwargs)
        finally:
            # Popen returns only after fork/exec setup has either committed a
            # child handle or failed. Pending lifecycle signals are delivered
            # to remember() here and honored by __exit__ after ownership cleanup.
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def __exit__(self, exc_type: Any, _exc: Any, _tb: Any) -> bool:
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)
        # Inner lease/flock/child contexts have all unwound before this outer
        # context exits. Replay only in the wrapper process after restoration;
        # default dispositions terminate it and custom/ignored ones are honored.
        for signum in self.received:
            signal.raise_signal(signum)
        return exc_type is _DeferredLifecycleSignal


class _DeferredLifecycleSignal(BaseException):
    """Internal pre-spawn unwind; never escapes after signal replay."""


def _safe_replay_tail(data: bytes | bytearray, limit: int = REPLAY_BYTES) -> bytearray:
    """Take a suffix beginning only at a UTF-8/SGR output-unit boundary."""
    raw = bytes(data)
    if len(raw) <= limit:
        return bytearray(raw)
    target = len(raw) - limit
    boundaries = [0]
    index = 0
    while index < len(raw):
        value = raw[index]
        if value == 0x1B and index + 1 < len(raw) and raw[index + 1] == ord("["):
            final = raw.find(b"m", index + 2)
            index = len(raw) if final < 0 else final + 1
        elif value < 0x80:
            index += 1
        else:
            if 0xC2 <= value <= 0xDF:
                width = 2
            elif 0xE0 <= value <= 0xEF:
                width = 3
            elif 0xF0 <= value <= 0xF4:
                width = 4
            else:
                width = 1
            candidate = raw[index : index + width]
            try:
                candidate.decode("utf-8")
            except UnicodeDecodeError:
                width = 1
            index += width
        boundaries.append(index)
    start = next((boundary for boundary in boundaries if boundary >= target), len(raw))
    return bytearray(raw[start:])


class _Client:
    def __init__(self, sock: socket.socket, hello: dict[str, Any]) -> None:
        self.sock = sock
        self.parser = FrameParser()
        self.send = bytearray(encode_json_frame(HELLO, hello))
        self.authenticated = False
        self.closing = False

    def queue(self, frame: bytes) -> None:
        self.send.extend(frame)
        if len(self.send) > MAX_CLIENT_QUEUE:
            raise ProtocolError("client output queue exceeded")


def _apply_resize(master: int, payload: dict[str, Any]) -> None:
    dimensions = valid_resize(payload)
    if dimensions is None:
        raise ProtocolError("invalid resize")
    cols, rows = dimensions
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _broker(project: str, request_id: str, lifecycle: "_HoldLifecycleSignals") -> int:
    paths = session_paths(project)
    # Under the held project flock, these can only belong to a dead broker.
    paths["socket"].unlink(missing_ok=True)
    paths["sidecar"].unlink(missing_ok=True)

    master, slave = pty.openpty()
    child: Optional[subprocess.Popen] = None
    listener: Optional[socket.socket] = None
    selector = selectors.DefaultSelector()
    client: Optional[_Client] = None
    sanitizer = AnsiSanitizer()
    replay = bytearray()
    pty_input = bytearray()
    master_open = True
    exit_code: Optional[int] = None
    exit_flush_deadline: Optional[float] = None
    session_id = uuid.uuid4().hex
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        os.set_blocking(master, False)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(paths["socket"]))
        paths["socket"].chmod(0o600)
        listener.listen(4)
        listener.setblocking(False)
        sidecar = {
            "project": project,
            "request_id": request_id,
            "session_id": session_id,
            "pid": os.getpid(),
            "started": started,
        }
        _write_sidecar(paths["sidecar"], sidecar)
        selector.register(listener, selectors.EVENT_READ, "listener")
        selector.register(master, selectors.EVENT_READ, "master")

        # Every fallible socket/sidecar/selector setup step precedes spawn. If
        # a later runtime failure occurs, finally passively reaps this exact
        # child before any PTY descriptor or ownership context is released.
        signer_argv = _gate_approve_argv(project, request_id)
        signer_environment = _login_environment()
        child = lifecycle.spawn(
            signer_argv,
            cwd=REPO,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=signer_environment,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave)
        slave = -1
        hello = {
            "project": project,
            "request_id": request_id,
            "session_id": session_id,
            "pid": os.getpid(),
            "lease_held": True,
            "signer": "running",
        }
        _log(project, request_id, f"started session={session_id} pid={child.pid}")

        def update_client_events() -> None:
            if client is None:
                return
            events = 0 if client.closing else selectors.EVENT_READ
            if client.send:
                events |= selectors.EVENT_WRITE
            if not events:
                detach_client()
                return
            try:
                selector.modify(client.sock, events, "client")
            except (KeyError, ValueError):
                pass

        def detach_client() -> None:
            nonlocal client
            if client is None:
                return
            with contextlib.suppress(Exception):
                selector.unregister(client.sock)
            with contextlib.suppress(OSError):
                client.sock.close()
            client = None

        def close_client(reason: str) -> None:
            if client is None:
                return
            try:
                client.queue(encode_json_frame(BYE, {"reason": reason}))
                client.closing = True
                update_client_events()
            except ProtocolError:
                detach_client()

        def queue_output(clean: bytes) -> None:
            if client is None or not client.authenticated:
                return
            for offset in range(0, len(clean), 65536):
                client.queue(encode_frame(OUT, clean[offset : offset + 65536]))

        while True:
            if exit_code is None:
                exit_code = child.poll()
                if exit_code is not None:
                    exit_flush_deadline = time.monotonic() + 1.0

            master_events = selectors.EVENT_READ | (selectors.EVENT_WRITE if pty_input else 0)
            if master_open:
                with contextlib.suppress(KeyError, ValueError):
                    selector.modify(master, master_events, "master")

            for key, mask in selector.select(timeout=0.1):
                if key.data == "listener":
                    try:
                        incoming, _ = listener.accept()
                    except BlockingIOError:
                        continue
                    if client is not None:
                        # A stable, framed refusal; this connection never
                        # becomes attached to the PTY.
                        with contextlib.suppress(OSError):
                            incoming.settimeout(0.2)
                            incoming.sendall(encode_json_frame(BYE, {"reason": "busy"}))
                        incoming.close()
                        continue
                    incoming.setblocking(False)
                    client = _Client(incoming, hello)
                    selector.register(incoming, selectors.EVENT_READ | selectors.EVENT_WRITE, "client")
                    continue

                if key.data == "master":
                    if mask & selectors.EVENT_WRITE and pty_input:
                        try:
                            count = os.write(master, pty_input)
                            del pty_input[:count]
                        except BlockingIOError:
                            pass
                        except OSError:
                            pty_input.clear()
                    if mask & selectors.EVENT_READ:
                        try:
                            raw = os.read(master, 65536)
                        except BlockingIOError:
                            raw = None
                        except OSError as exc:
                            raw = b"" if exc.errno == errno.EIO else None
                        if raw == b"":
                            clean = sanitizer.flush()
                            if clean:
                                replay.extend(clean)
                                replay = _safe_replay_tail(replay)
                                if client is not None and client.authenticated:
                                    try:
                                        queue_output(clean)
                                    except ProtocolError:
                                        detach_client()
                            master_open = False
                            with contextlib.suppress(Exception):
                                selector.unregister(master)
                        elif raw:
                            clean = sanitizer.feed(raw)
                            if clean:
                                replay.extend(clean)
                                if len(replay) > REPLAY_BYTES:
                                    replay = _safe_replay_tail(replay)
                                if client is not None and client.authenticated:
                                    try:
                                        queue_output(clean)
                                        update_client_events()
                                    except ProtocolError:
                                        detach_client()
                    continue

                if key.data != "client" or client is None:
                    continue
                if mask & selectors.EVENT_READ:
                    try:
                        chunk = client.sock.recv(65536)
                    except BlockingIOError:
                        chunk = None
                    except OSError:
                        chunk = b""
                    if chunk == b"":
                        try:
                            client.parser.feed_eof()
                        except ProtocolError:
                            close_client("protocol")
                        else:
                            detach_client()
                        continue
                    if chunk:
                        try:
                            frames = client.parser.feed(chunk)
                            for frame_type, payload in frames:
                                if not client.authenticated:
                                    if frame_type != HELLO:
                                        raise ProtocolError("client HELLO must be first")
                                    identity = decode_json_payload(HELLO, payload)
                                    if (
                                        set(identity) != {"project", "request_id", "token_ok"}
                                        or identity.get("project") != project
                                        or identity.get("request_id") != request_id
                                        or identity.get("token_ok") is not True
                                    ):
                                        close_client("mismatch")
                                        break
                                    client.authenticated = True
                                    if replay:
                                        client.queue(encode_frame(OUT, bytes(replay)))
                                    continue
                                if frame_type == IN:
                                    pty_input.extend(payload)
                                    if len(pty_input) > MAX_CLIENT_QUEUE:
                                        raise ProtocolError("pty input queue exceeded")
                                elif frame_type == RESIZE:
                                    _apply_resize(master, decode_json_payload(RESIZE, payload))
                                else:
                                    raise ProtocolError("unexpected client frame")
                        except ProtocolError:
                            close_client("protocol")
                        update_client_events()
                if client is not None and mask & selectors.EVENT_WRITE and client.send:
                    try:
                        sent = client.sock.send(client.send)
                        del client.send[:sent]
                    except BlockingIOError:
                        pass
                    except OSError:
                        detach_client()
                        continue
                    if client is not None and client.closing and not client.send:
                        detach_client()
                    else:
                        update_client_events()

            if exit_code is not None and (not master_open or time.monotonic() >= (exit_flush_deadline or 0)):
                if master_open:
                    clean = sanitizer.flush()
                    if clean and client is not None and client.authenticated:
                        with contextlib.suppress(ProtocolError):
                            queue_output(clean)
                    master_open = False
                    with contextlib.suppress(Exception):
                        selector.unregister(master)
                if client is not None:
                    with contextlib.suppress(ProtocolError):
                        client.queue(encode_json_frame(STATUS, {"signer": "exited", "exit_code": exit_code}))
                        client.queue(encode_json_frame(BYE, {"reason": "exited", "exit_code": exit_code}))
                        client.closing = True
                        update_client_events()
                    # Give a connected page a brief chance to consume terminal
                    # tail/status. This does not extend or cancel the signer.
                    flush_until = time.monotonic() + 1.0
                    while client is not None and client.send and time.monotonic() < flush_until:
                        _, writable, _ = __import__("select").select([], [client.sock], [], 0.05)
                        if not writable:
                            continue
                        try:
                            sent = client.sock.send(client.send)
                            del client.send[:sent]
                        except (BlockingIOError, OSError):
                            break
                return int(exit_code)
    finally:
        # This precedes every descriptor close and therefore every possible
        # PTY hangup. It never writes input or signals/kills the child.
        if child is not None:
            _reap_exact_child(child)
        if client is not None:
            with contextlib.suppress(Exception):
                selector.unregister(client.sock)
            with contextlib.suppress(OSError):
                client.sock.close()
        if listener is not None:
            with contextlib.suppress(Exception):
                selector.unregister(listener)
            listener.close()
        if master_open:
            with contextlib.suppress(Exception):
                selector.unregister(master)
        with contextlib.suppress(OSError):
            os.close(master)
        if slave >= 0:
            with contextlib.suppress(OSError):
                os.close(slave)
        selector.close()
        paths["socket"].unlink(missing_ok=True)
        paths["sidecar"].unlink(missing_ok=True)
        if child is not None:
            _log(project, request_id, f"exited session={session_id} exit_code={child.returncode}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--broker", action="store_true")
    args = parser.parse_args(argv)

    if not args.broker and not sys.stdin.isatty():
        print("gate_sign: foreground signing requires a terminal", file=sys.stderr)
        return 2
    try:
        request_id = validate_request_id(args.request)
        root = resolve_project_root(args.project)
    except RunError as exc:
        print(f"gate_sign: {exc}", file=sys.stderr)
        return 2

    with _HoldLifecycleSignals() as lifecycle:
        try:
            lease = run_lease.acquire(root, _lease_minutes(root))
        except run_lease.LeaseHeldError:
            print(f"a run holds the project lease (pid {_lease_owner_pid(root)}); wait for it to block or finish")
            return 5

        try:
            with lease:
                with _project_flock(args.project):
                    _validate_pending(root, args.project, request_id)
                    return (
                        _broker(args.project, request_id, lifecycle)
                        if args.broker
                        else _foreground(args.project, request_id, lifecycle)
                    )
        except RunError as exc:
            print(f"gate_sign: {exc}", file=sys.stderr)
            return 6
        except OSError as exc:
            print(f"gate_sign: terminal broker failed: {exc}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
