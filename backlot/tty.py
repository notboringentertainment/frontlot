"""Detached signing-terminal protocol and Backlot WebSocket relay.

``scripts/gate_sign.py`` owns the PTY, child, project lease and interprocess
lock.  This module gives Backlot a disposable authenticated attachment to that
broker.  Closing a relay never writes to or signals the signer.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import stat
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from fastapi import FastAPI, WebSocket
else:
    FastAPI = Any
    WebSocket = Any


# Stable wire types: u8 type | u32 big-endian payload length | payload.
HELLO = 1
IN = 2
OUT = 3
RESIZE = 4
STATUS = 5
BYE = 6

FRAME_HELLO = HELLO
FRAME_IN = IN
FRAME_OUT = OUT
FRAME_RESIZE = RESIZE
FRAME_STATUS = STATUS
FRAME_BYE = BYE
TYPE_HELLO = HELLO
TYPE_IN = IN
TYPE_OUT = OUT
TYPE_RESIZE = RESIZE
TYPE_STATUS = STATUS
TYPE_BYE = BYE

FRAME_HEADER = struct.Struct("!BI")
MAX_IN = 4096
MAX_OUT = 65536
MAX_JSON = 4096
MAX_WS_PENDING = 256 * 1024
RESIZE_COLS = (10, 500)
RESIZE_ROWS = (3, 300)

_KNOWN_TYPES = {HELLO, IN, OUT, RESIZE, STATUS, BYE}
_JSON_TYPES = {HELLO, RESIZE, STATUS, BYE}


class ProtocolError(ValueError):
    """A framed peer violated the bounded IPC protocol."""


class IdentityMismatch(RuntimeError):
    pass


class BrokerBusy(RuntimeError):
    pass


class BrokerUnavailable(RuntimeError):
    pass


class RunLeaseHeld(RuntimeError):
    pass


class CleanEOF(EOFError):
    """Peer closed cleanly between complete frames."""


class RequestNotFound(FileNotFoundError):
    pass


class RequestInvalid(ValueError):
    pass


def frame_limit(frame_type: int) -> int:
    if frame_type == IN:
        return MAX_IN
    if frame_type == OUT:
        return MAX_OUT
    if frame_type in _JSON_TYPES:
        return MAX_JSON
    raise ProtocolError("unknown frame type")


def encode_frame(frame_type: int, payload: bytes = b"") -> bytes:
    payload = bytes(payload)
    limit = frame_limit(frame_type)
    if len(payload) > limit:
        raise ProtocolError("oversized frame")
    return FRAME_HEADER.pack(frame_type, len(payload)) + payload


def encode_json_frame(frame_type: int, payload: dict[str, Any]) -> bytes:
    if frame_type not in _JSON_TYPES:
        raise ProtocolError("frame type is not JSON")
    try:
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("invalid JSON payload") from exc
    return encode_frame(frame_type, raw)


def decode_json_payload(frame_type: int, payload: bytes) -> dict[str, Any]:
    if frame_type not in _JSON_TYPES or len(payload) > MAX_JSON:
        raise ProtocolError("invalid JSON frame")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("malformed JSON frame") from exc
    if not isinstance(value, dict):
        raise ProtocolError("JSON frame must carry an object")
    return value


class FrameParser:
    """Incremental parser for fragmented and coalesced stream frames."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._expected: Optional[tuple[int, int]] = None

    def feed(self, data: bytes | bytearray | memoryview) -> list[tuple[int, bytes]]:
        self._buffer.extend(data)
        frames: list[tuple[int, bytes]] = []
        while True:
            if self._expected is None:
                if len(self._buffer) < FRAME_HEADER.size:
                    break
                frame_type, length = FRAME_HEADER.unpack(self._buffer[: FRAME_HEADER.size])
                del self._buffer[: FRAME_HEADER.size]
                if frame_type not in _KNOWN_TYPES:
                    raise ProtocolError("unknown frame type")
                if length > frame_limit(frame_type):
                    raise ProtocolError("oversized frame")
                self._expected = (frame_type, length)
            frame_type, length = self._expected
            if len(self._buffer) < length:
                break
            payload = bytes(self._buffer[:length])
            del self._buffer[:length]
            self._expected = None
            frames.append((frame_type, payload))
        return frames

    def feed_eof(self) -> None:
        if self._expected is not None or self._buffer:
            raise ProtocolError("truncated frame")

    close = feed_eof


pack_frame = encode_frame
pack_json_frame = encode_json_frame


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    try:
        header = await reader.readexactly(FRAME_HEADER.size)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            raise CleanEOF("clean frame-stream EOF") from exc
        raise ProtocolError("truncated frame") from exc
    frame_type, length = FRAME_HEADER.unpack(header)
    if frame_type not in _KNOWN_TYPES:
        raise ProtocolError("unknown frame type")
    if length > frame_limit(frame_type):
        raise ProtocolError("oversized frame")
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("truncated frame") from exc
    return frame_type, payload


def valid_resize(payload: Any) -> Optional[tuple[int, int]]:
    if not isinstance(payload, dict):
        return None
    cols, rows = payload.get("cols"), payload.get("rows")
    if isinstance(cols, bool) or isinstance(rows, bool):
        return None
    if not isinstance(cols, int) or not isinstance(rows, int):
        return None
    if not RESIZE_COLS[0] <= cols <= RESIZE_COLS[1]:
        return None
    if not RESIZE_ROWS[0] <= rows <= RESIZE_ROWS[1]:
        return None
    return cols, rows


def metadata_root() -> Path:
    """Private runtime root, overridable for hermetic process tests."""
    configured = os.environ.get("OPENMONTAGE_GATES_DIR")
    root = Path(configured) if configured else Path.home() / ".openmontage" / "backlot"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def sessions_dir() -> Path:
    directory = metadata_root() / "sessions"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


def session_paths(project: str) -> dict[str, Path]:
    # Callers validate the project grammar before reaching here.  Keep a
    # defensive local check because these paths are cleanup targets.
    from lib.run_common import ENTITY_ID_RE

    if not isinstance(project, str) or ENTITY_ID_RE.fullmatch(project) is None:
        raise ValueError("invalid project slug")
    base = sessions_dir() / project
    return {
        "socket": base.with_suffix(".sock"),
        "lock": base.with_suffix(".lock"),
        "sidecar": base.with_suffix(".session.json"),
    }


def _safe_sidecar(path: Path) -> Optional[dict[str, Any]]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON:
            return None
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("project"), str) or not isinstance(value.get("request_id"), str):
        return None
    if not isinstance(value.get("session_id"), str) or not isinstance(value.get("pid"), int):
        return None
    if not isinstance(value.get("started"), str):
        return None
    return value


def list_open_sessions(project: Optional[str] = None) -> list[dict[str, Any]]:
    """List advisory live-session metadata without attaching to a broker."""
    directory = sessions_dir()
    candidates = [session_paths(project)["sidecar"]] if project else sorted(directory.glob("*.session.json"))
    rows: list[dict[str, Any]] = []
    for sidecar in candidates:
        value = _safe_sidecar(sidecar)
        if value is None:
            continue
        try:
            paths = session_paths(value["project"])
            socket_info = paths["socket"].lstat()
        except (OSError, ValueError):
            continue
        if sidecar != paths["sidecar"] or not stat.S_ISSOCK(socket_info.st_mode):
            continue
        rows.append({
            "project": value["project"],
            "request_id": value["request_id"],
            "session_id": value["session_id"],
            "pid": value["pid"],
            "started": value["started"],
        })
    return rows


@dataclass
class _LiveRelay:
    project: str
    websocket: WebSocket
    writer: Optional[asyncio.StreamWriter] = None

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self.writer = None
        try:
            await self.websocket.close(code=1012, reason="server shutting down")
        except Exception:
            pass


class TTYRegistry:
    """One relay reservation per project in this single-worker server."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.sessions: dict[str, _LiveRelay] = {}
        self.spawned: set[subprocess.Popen] = set()
        self.reapers: set[asyncio.Task] = set()

    def track_broker(self, process: subprocess.Popen) -> None:
        self.spawned.add(process)

        async def reap_passively() -> None:
            try:
                while process.poll() is None:
                    await asyncio.sleep(0.2)
                # poll() has waitpid-reaped the exact child.
            finally:
                self.spawned.discard(process)

        task = asyncio.create_task(reap_passively())
        self.reapers.add(task)
        task.add_done_callback(self.reapers.discard)

    async def reserve(self, project: str, websocket: WebSocket) -> Optional[_LiveRelay]:
        async with self.lock:
            if project in self.sessions:
                return None
            relay = _LiveRelay(project, websocket)
            self.sessions[project] = relay
            return relay

    async def release(self, project: str, relay: _LiveRelay) -> None:
        async with self.lock:
            if self.sessions.get(project) is relay:
                self.sessions.pop(project, None)

    async def shutdown(self) -> None:
        async with self.lock:
            relays = list(self.sessions.values())
            self.sessions.clear()
        await asyncio.gather(*(relay.close() for relay in relays), return_exceptions=True)
        # Deliberately do not wait for, terminate, signal, or otherwise touch
        # detached brokers. Cancel only our passive polling coroutines; live
        # brokers remain attachable after a server restart.
        reapers = list(self.reapers)
        for task in reapers:
            task.cancel()
        await asyncio.gather(*reapers, return_exceptions=True)
        for process in list(self.spawned):
            process.poll()  # one final nonblocking reap check only
        self.reapers.clear()
        self.spawned.clear()


_MAX_REQUEST_BYTES = 128 * 1024
_REQUEST_STATES = (("pending", None), ("done", "done"), ("declined", "declined"), ("abandoned", "abandoned"))
_REQUIRED_REQUEST_FIELDS = ("request_id", "project_id", "stage", "scope", "kind", "summary")
_SELECTION_KINDS = {"headshot"}
_OPEN_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_OPEN_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


def validate_request_structure(data: Any, *, project: str, request_id: str, stem: str) -> dict[str, Any]:
    """Pure structural parity with ``gate_approve.load_request`` plus identity.

    It deliberately does not import or call the handler.  A pending committed
    decline is structurally valid so the handler can finish its crash-recovery
    move and refuse it before display; this validator never makes a decision.
    """
    from lib.receipts import APPROVAL_KINDS
    from lib.run_common import RunError, validate_request_id

    if not isinstance(data, dict):
        raise RequestInvalid("gate request must be an object")
    missing = [name for name in _REQUIRED_REQUEST_FIELDS if name not in data]
    if missing:
        raise RequestInvalid(f"gate request missing fields {missing}")
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in APPROVAL_KINDS:
        raise RequestInvalid("unknown gate request kind")
    if kind in _SELECTION_KINDS and data.get("approval_record") is not None:
        raise RequestInvalid("selection request must not carry approval_record")
    if data.get("approval_record") is not None and not isinstance(data.get("approval_record"), dict):
        raise RequestInvalid("approval_record hint must be an object")
    try:
        validated = validate_request_id(data.get("request_id"))
    except RunError as exc:
        raise RequestInvalid("invalid gate request id") from exc
    if validated != stem or validated != request_id:
        raise RequestInvalid("gate request identity mismatch")
    if data.get("project_id") != project:
        raise RequestInvalid("cross-project gate request")
    return data


def _open_dir_at(parent_fd: int, name: str, *, optional: bool = False) -> Optional[int]:
    try:
        descriptor = os.open(name, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if optional:
            return None
        raise RequestNotFound("gate request directory not found")
    except OSError as exc:
        raise RequestInvalid("symlink or invalid gate request directory") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RequestInvalid("gate request component is not a directory")
    return descriptor


def _open_regular_at(directory_fd: int, name: str) -> Optional[int]:
    try:
        descriptor = os.open(name, _OPEN_FILE_FLAGS, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RequestInvalid("symlink or invalid gate request file") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RequestInvalid("gate request is not a regular file")
    return descriptor


def _read_bounded_descriptor(descriptor: int) -> bytes:
    info = os.fstat(descriptor)
    if info.st_size > _MAX_REQUEST_BYTES:
        raise RequestInvalid("gate request is too large")
    chunks: list[bytes] = []
    remaining = _MAX_REQUEST_BYTES + 1
    while remaining:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) > _MAX_REQUEST_BYTES:
        raise RequestInvalid("gate request is too large")
    return raw


def load_safe_pending_request(project_dir: Path, project: str, request_id: str) -> dict[str, Any]:
    """Open every path component fd-relatively and read the checked inode."""
    from lib.run_common import RunError, validate_request_id

    try:
        validate_request_id(request_id)
    except RunError as exc:
        raise RequestInvalid("invalid request id") from exc
    if Path(project_dir).name != project:
        raise RequestInvalid("project directory identity mismatch")
    descriptors: list[int] = []
    pending_raw: Optional[bytes] = None
    found: list[str] = []
    try:
        try:
            project_fd = os.open(project_dir, _OPEN_DIRECTORY_FLAGS)
        except OSError as exc:
            raise RequestInvalid("symlink or invalid project directory") from exc
        descriptors.append(project_fd)
        gates_fd = _open_dir_at(project_fd, ".gate-requests")
        assert gates_fd is not None
        descriptors.append(gates_fd)
        filename = f"{request_id}.json"
        for state_name, directory_name in _REQUEST_STATES:
            state_fd = gates_fd if directory_name is None else _open_dir_at(gates_fd, directory_name, optional=True)
            if state_fd is None:
                continue
            if state_fd != gates_fd:
                descriptors.append(state_fd)
            request_fd = _open_regular_at(state_fd, filename)
            if request_fd is None:
                continue
            descriptors.append(request_fd)
            found.append(state_name)
            if state_name == "pending":
                pending_raw = _read_bounded_descriptor(request_fd)
        if not found:
            raise RequestNotFound("gate request not found")
        if found != ["pending"] or pending_raw is None:
            raise RequestInvalid("gate request is not uniquely pending")
        try:
            data = json.loads(pending_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestInvalid("malformed gate request") from exc
        return validate_request_structure(data, project=project, request_id=request_id, stem=request_id)
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _request_precondition(project_dir: Path, project: str, request_id: str) -> dict[str, Any]:
    """Compatibility-private name used by focused relay diagnostics."""
    return load_safe_pending_request(project_dir, project, request_id)


def _live_run_lease(project_dir: Path) -> bool:
    """Read the lease without acquiring, reclaiming, or changing it."""
    from lib import run_lease

    path = run_lease.lease_path(project_dir)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if not stat.S_ISREG(info.st_mode):
        return True
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return not isinstance(record, dict) or not run_lease.owner_is_dead(record)


def _signing_session_evidence(project: str, request_id: str) -> bool:
    """Passively observe matching socket/sidecar publication evidence."""
    paths = session_paths(project)
    try:
        socket_live = stat.S_ISSOCK(paths["socket"].lstat().st_mode)
    except OSError:
        socket_live = False
    sidecar = _safe_sidecar(paths["sidecar"])
    sidecar_matches = bool(
        sidecar
        and sidecar.get("project") == project
        and sidecar.get("request_id") == request_id
    )
    return socket_live and sidecar_matches


async def _reconcile_live_lease(
    project: str,
    request_id: str,
    *,
    timeout: float = 5.0,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, dict[str, Any]]:
    """Distinguish a starting signer from an unrelated project run."""
    deadline = time.monotonic() + timeout
    saw_matching_session = False
    delay = 0.02
    while time.monotonic() < deadline:
        try:
            return await _attach(project, request_id)
        except BrokerBusy:
            raise
        except IdentityMismatch:
            raise
        except ProtocolError:
            raise
        except BrokerUnavailable:
            saw_matching_session |= _signing_session_evidence(project, request_id)
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 0.2)
    saw_matching_session |= _signing_session_evidence(project, request_id)
    if saw_matching_session:
        raise BrokerBusy("a signing terminal is already open for this project")
    raise RunLeaseHeld("unrelated project run lease is held")


def _sidecar_matches(hello: dict[str, Any], expected_project: str, expected_request: str) -> bool:
    if hello.get("project") != expected_project or hello.get("request_id") != expected_request:
        return False
    if hello.get("signer") not in {"starting", "running", "exited"}:
        return False
    if not isinstance(hello.get("lease_held"), bool):
        return False
    if not isinstance(hello.get("session_id"), str) or not isinstance(hello.get("pid"), int):
        return False
    sidecar = _safe_sidecar(session_paths(expected_project)["sidecar"])
    if sidecar is None:
        return False
    return all(sidecar.get(name) == hello.get(name) for name in ("project", "request_id", "session_id", "pid"))


async def _best_effort_bye(writer: asyncio.StreamWriter, reason: str) -> None:
    """Send one bounded protocol close without letting a bad peer stall us."""
    try:
        writer.write(encode_json_frame(BYE, {"reason": reason}))
        await asyncio.wait_for(writer.drain(), timeout=0.25)
    except (asyncio.TimeoutError, ConnectionError, OSError, ProtocolError):
        pass


async def _attach(project: str, request_id: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, dict[str, Any]]:
    socket_path = session_paths(project)["socket"]
    try:
        info = socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode):
            raise BrokerUnavailable("broker socket is not a unix socket")
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
    except (FileNotFoundError, ConnectionError, OSError) as exc:
        raise BrokerUnavailable("broker socket unavailable") from exc

    try:
        frame_type, payload = await asyncio.wait_for(read_frame(reader), timeout=1.0)
        if frame_type == BYE:
            reason = decode_json_payload(BYE, payload).get("reason")
            if reason == "busy":
                raise BrokerBusy("broker already has an attached client")
            raise BrokerUnavailable("broker refused attachment")
        if frame_type != HELLO:
            raise ProtocolError("broker HELLO must be first")
        hello = decode_json_payload(HELLO, payload)
        # Bind normally precedes the sidecar's atomic replace by only a few
        # instructions.  Wait briefly for that publication without treating
        # a concurrent broker startup as a contradictory identity.
        sidecar_deadline = time.monotonic() + 0.5
        while _safe_sidecar(session_paths(project)["sidecar"]) is None and time.monotonic() < sidecar_deadline:
            await asyncio.sleep(0.01)
        if not _sidecar_matches(hello, project, request_id):
            raise IdentityMismatch("session identity mismatch")
        writer.write(encode_json_frame(HELLO, {
            "project": project,
            "request_id": request_id,
            "token_ok": True,
        }))
        await writer.drain()
        return reader, writer, hello
    except CleanEOF as exc:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        raise BrokerUnavailable("broker closed before HELLO") from exc
    except ProtocolError:
        await _best_effort_bye(writer, "protocol")
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        raise
    except Exception:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        raise


def _spawn_broker(registry: TTYRegistry, project_dir: Path, request_id: str) -> subprocess.Popen:
    from lib.run_common import gate_invocation

    invocation = gate_invocation(project_dir, request_id)
    process = subprocess.Popen(
        [*invocation["argv"], "--broker"],
        cwd=invocation["cwd"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    registry.track_broker(process)
    return process


async def _connect_or_spawn(
    registry: TTYRegistry,
    project_dir: Path,
    project: str,
    request_id: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, dict[str, Any]]:
    try:
        return await _attach(project, request_id)
    except BrokerBusy:
        raise
    except IdentityMismatch:
        # A valid socket with a contradictory identity is never replaced or
        # guessed around.  HELLO is authoritative and the sidecar must agree.
        raise
    except BrokerUnavailable:
        process = _spawn_broker(registry, project_dir, request_id)

    deadline = time.monotonic() + 5.0
    last_identity_error: Optional[IdentityMismatch] = None
    losing_lease_race = False
    saw_matching_session = False
    delay = 0.02
    while time.monotonic() < deadline:
        try:
            return await _attach(project, request_id)
        except BrokerBusy:
            raise
        except IdentityMismatch as exc:
            # During startup bind precedes the atomic sidecar write; tolerate
            # that tiny readiness interval, but never attach without a match.
            last_identity_error = exc
        except BrokerUnavailable:
            saw_matching_session |= _signing_session_evidence(project, request_id)
        code = process.poll()
        if code is not None:
            if code == 5:
                # Another server may have won the lease immediately before it
                # publishes lock/socket/sidecar evidence. Keep reconciling.
                losing_lease_race = True
            elif not saw_matching_session and not _signing_session_evidence(project, request_id):
                if last_identity_error is not None:
                    raise last_identity_error
                raise BrokerUnavailable(f"signing broker exited with status {code}")
        await asyncio.sleep(delay)
        delay = min(delay * 1.6, 0.25)
    if last_identity_error is not None:
        raise last_identity_error
    saw_matching_session |= _signing_session_evidence(project, request_id)
    if saw_matching_session:
        raise BrokerBusy("a signing terminal is already open for this project")
    if losing_lease_race or _live_run_lease(project_dir):
        raise RunLeaseHeld("unrelated project run lease is held")
    raise BrokerUnavailable("signing broker did not become ready")


def _log(message: str) -> None:
    # Import lazily: server imports this module while constructing the app.
    from backlot.server import server_log

    server_log(message)


async def _websocket_input(
    websocket: WebSocket,
    queue: asyncio.Queue[tuple[str, Any]],
    input_ready: asyncio.Event,
) -> None:
    """Read page frames early so pre-refresh human bytes are provably dropped."""
    try:
        while True:
            message = await websocket.receive()
            kind = message.get("type")
            if kind == "websocket.disconnect":
                await queue.put(("disconnect", None))
                return
            binary = message.get("bytes")
            if binary is not None:
                # Input is intentionally discarded, not buffered, until the
                # refreshed evidence packet has reached the page.
                if not input_ready.is_set():
                    continue
                if len(binary) > MAX_IN:
                    await queue.put(("protocol", None))
                    return
                await queue.put(("input", bytes(binary)))
                continue
            text = message.get("text")
            if text is None:
                continue
            try:
                control = json.loads(text)
            except (TypeError, ValueError):
                continue
            if not isinstance(control, dict) or control.get("type") != "resize":
                continue
            dimensions = valid_resize(control)
            if dimensions is not None:
                await queue.put(("resize", {"cols": dimensions[0], "rows": dimensions[1]}))
    except Exception:
        await queue.put(("disconnect", None))


async def _page_to_broker(
    incoming: asyncio.Queue[tuple[str, Any]],
    writer: asyncio.StreamWriter,
) -> None:
    while True:
        kind, payload = await incoming.get()
        if kind == "disconnect":
            return
        if kind == "protocol":
            raise ProtocolError("oversized websocket input")
        if kind == "input":
            writer.write(encode_frame(IN, payload))
        elif kind == "resize":
            writer.write(encode_json_frame(RESIZE, payload))
        else:
            continue
        await writer.drain()


async def _broker_to_page_queue(
    reader: asyncio.StreamReader,
    outgoing: asyncio.Queue[tuple[str, Any, int]],
    space_available: asyncio.Condition,
    pending: list[int],
) -> None:
    while True:
        try:
            frame_type, payload = await read_frame(reader)
        except CleanEOF:
            return
        if frame_type == OUT:
            size = len(payload)
            async with space_available:
                await space_available.wait_for(lambda: pending[0] + size <= MAX_WS_PENDING)
                pending[0] += size
            await outgoing.put(("binary", payload, size))
            continue
        if frame_type == STATUS:
            event = decode_json_payload(STATUS, payload)
            await outgoing.put(("json", {"type": "status", **event}, 0))
            continue
        if frame_type == BYE:
            event = decode_json_payload(BYE, payload)
            await outgoing.put(("json", {"type": "bye", **event}, 0))
            await outgoing.join()
            return
        raise ProtocolError("unexpected broker frame")


async def _page_sender(
    websocket: WebSocket,
    outgoing: asyncio.Queue[tuple[str, Any, int]],
    space_available: asyncio.Condition,
    pending: list[int],
) -> None:
    while True:
        kind, payload, size = await outgoing.get()
        try:
            if kind == "binary":
                await websocket.send_bytes(payload)
            else:
                await websocket.send_json(payload)
        finally:
            if size:
                async with space_available:
                    pending[0] -= size
                    space_available.notify_all()
            outgoing.task_done()


async def _relay(
    websocket: WebSocket,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    incoming: asyncio.Queue[tuple[str, Any]],
) -> None:
    outgoing: asyncio.Queue[tuple[str, Any, int]] = asyncio.Queue()
    space_available = asyncio.Condition()
    pending = [0]
    tasks = {
        asyncio.create_task(_page_to_broker(incoming, writer)),
        asyncio.create_task(_broker_to_page_queue(reader, outgoing, space_available, pending)),
        asyncio.create_task(_page_sender(websocket, outgoing, space_available, pending)),
    }
    try:
        done, waiting = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exception = task.exception()
            if exception is not None:
                if isinstance(exception, ProtocolError):
                    await _best_effort_bye(writer, "protocol")
                raise exception
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _first_token(websocket: WebSocket, expected: Optional[str]) -> bool:
    try:
        message = await websocket.receive()
    except Exception:
        return False
    if message.get("type") != "websocket.receive" or message.get("text") is None:
        return False
    try:
        payload = json.loads(message["text"])
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and isinstance(expected, str) and payload.get("k") == expected


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        pass


async def _tty_websocket(websocket: WebSocket, project_id: str, request_id: str) -> None:
    app = websocket.scope["app"]
    port = app.state.server_port
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    origin = websocket.headers.get("origin")
    await websocket.accept()
    if origin not in allowed_origins:
        await _close(websocket, 4403, "origin not allowed")
        return
    if not await _first_token(websocket, app.state.capability_token):
        await _close(websocket, 4401, "authentication required")
        return

    registry: TTYRegistry = app.state.tty_registry
    # This is the first await after authentication.  No filesystem or broker
    # work can race ahead of the in-process reservation.
    live = await registry.reserve(project_id, websocket)
    if live is None:
        await _close(websocket, 4409, "a signing terminal is already open for this project")
        _log(f"tty refused project={project_id} request={request_id} reason=busy")
        return

    input_ready = asyncio.Event()
    incoming: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=64)
    page_reader = asyncio.create_task(_websocket_input(websocket, incoming, input_ready))
    writer: Optional[asyncio.StreamWriter] = None
    try:
        from backlot import server as server_module

        try:
            project_dir = server_module._safe_project_dir(project_id)
        except Exception:
            await _close(websocket, 4404, "unknown project")
            _log(f"tty refused project={project_id} request={request_id} reason=unknown-project")
            return
        try:
            _request_precondition(project_dir, project_id, request_id)
        except FileNotFoundError:
            await _close(websocket, 4404, "gate request not found")
            _log(f"tty refused project={project_id} request={request_id} reason=request-not-found")
            return
        except ValueError:
            await _close(websocket, 4400, "gate request is not safely pending")
            _log(f"tty refused project={project_id} request={request_id} reason=request-invalid")
            return
        try:
            # A restarted server must attach to a live signer even though that
            # broker correctly owns the project lease.  Try the socket first;
            # only an unavailable socket reaches the pre-spawn lease check.
            try:
                broker_reader, writer, hello = await _attach(project_id, request_id)
            except BrokerUnavailable:
                if _live_run_lease(project_dir):
                    broker_reader, writer, hello = await _reconcile_live_lease(project_id, request_id)
                else:
                    broker_reader, writer, hello = await _connect_or_spawn(
                        registry, project_dir, project_id, request_id
                    )
        except BrokerBusy:
            await _close(websocket, 4409, "a signing terminal is already open for this project")
            _log(f"tty refused project={project_id} request={request_id} reason=broker-busy")
            return
        except IdentityMismatch:
            await _close(websocket, 4409, "session identity mismatch")
            _log(f"tty refused project={project_id} request={request_id} reason=session-identity-mismatch")
            return
        except RunLeaseHeld:
            await _close(websocket, 4423, "a run holds the project lease")
            _log(f"tty refused project={project_id} request={request_id} reason=run-lease")
            return
        except BrokerUnavailable:
            await _close(websocket, 1011, "signing broker unavailable")
            _log(f"tty refused project={project_id} request={request_id} reason=broker-unavailable")
            return

        live.writer = writer
        if not hello.get("lease_held"):
            await _close(websocket, 1011, "signing broker did not hold the lease")
            _log(f"tty refused project={project_id} request={request_id} reason=lease-not-held")
            return

        # This deliberately runs synchronously while the detached broker owns
        # the project lease.  No input event is enabled or buffered yet.
        refreshed = server_module.load_gate_detail(project_dir, request_id)
        if refreshed.get("state") != "pending" or refreshed.get("error") == "invalid governed project or request":
            await _close(websocket, 4400, "gate request changed before signing")
            _log(f"tty refused project={project_id} request={request_id} reason=refresh-invalid")
            return
        await websocket.send_json({"type": "packet_refreshed", "packet": refreshed})
        packet = refreshed.get("packet")
        if refreshed.get("packet_error") or not isinstance(packet, dict) or packet.get("packet_error"):
            await _close(websocket, 4422, "gate evidence is unavailable")
            _log(f"tty refused project={project_id} request={request_id} reason=packet-error")
            return
        # Let the already-running reader drain frames queued before refresh.
        # Conservatively dropping a byte at this boundary is safe; forwarding
        # a byte typed against stale evidence is not.
        await asyncio.sleep(0)
        await websocket.send_json({"type": "input_ready"})
        input_ready.set()
        _log(f"tty opened project={project_id} request={request_id} session={hello.get('session_id')}")
        await _relay(websocket, broker_reader, writer, incoming)
        _log(f"tty closed project={project_id} request={request_id} reason=relay-ended")
    except (ConnectionError, OSError):
        _log(f"tty closed project={project_id} request={request_id} reason=disconnected")
    except ProtocolError:
        await _close(websocket, 1002, "terminal protocol error")
        _log(f"tty closed project={project_id} request={request_id} reason=protocol")
    finally:
        page_reader.cancel()
        await asyncio.gather(page_reader, return_exceptions=True)
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            live.writer = None
        await registry.release(project_id, live)


def install_tty(app: FastAPI) -> TTYRegistry:
    """Install the relay, discovery routes, and per-app lifecycle registry."""
    from fastapi import WebSocket as FastAPIWebSocket

    registry = TTYRegistry()
    app.state.tty_registry = registry

    async def gate_tty(websocket: WebSocket, project_id: str, request_id: str) -> None:
        await _tty_websocket(websocket, project_id, request_id)

    # Keep FastAPI imports lazy so the standalone stdlib broker can import the
    # shared framing helpers without importing the web stack.
    gate_tty.__annotations__["websocket"] = FastAPIWebSocket
    app.websocket("/api/project/{project_id}/gate/{request_id}/tty")(gate_tty)

    @app.get("/api/gate-sessions")
    async def gate_sessions() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_open_sessions)

    @app.get("/api/project/{project_id}/gate-sessions")
    async def project_gate_sessions(project_id: str) -> list[dict[str, Any]]:
        # The project resolver is applied by the normal server API before the
        # advisory external metadata is exposed for a requested slug.
        from backlot import server as server_module

        server_module._safe_project_dir(project_id)
        return await asyncio.to_thread(list_open_sessions, project_id)

    return registry


async def shutdown_tty(app: FastAPI) -> None:
    registry = getattr(app.state, "tty_registry", None)
    if registry is not None:
        await registry.shutdown()
