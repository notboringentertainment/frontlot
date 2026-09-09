"""Framing, WebSocket admission, and disposable relay tests for Backlot TTY."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient

from backlot import server as server_mod
from backlot import state as state_mod
from backlot import tty
from backlot.tty import (
    BYE,
    HELLO,
    IN,
    MAX_OUT,
    MAX_WS_PENDING,
    OUT,
    RESIZE,
    STATUS,
    BrokerUnavailable,
    FrameParser,
    IdentityMismatch,
    ProtocolError,
    TTYRegistry,
    decode_json_payload,
    encode_frame,
    encode_json_frame,
    read_frame,
)
from lib import run_lease
from tests.backlot.tty_helpers import (
    DisposableResources,
    REQUEST,
    _WRAPPER_BOOTSTRAP,
    attach,
    attach_when_free,
    collect_until_bye,
    fake_signer,
    launch_fake_broker,
    process_env,
    recv_frame,
    wait_until,
    write_project,
)
from tests.lib.look_lock_helpers import PROJECT


TOKEN = "t" * 43
PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"


def test_frame_parser_handles_every_fragmented_header_and_payload_boundary() -> None:
    frames = encode_frame(IN, b"human") + encode_json_frame(RESIZE, {"cols": 80, "rows": 24})
    expected = [(IN, b"human"), (RESIZE, b'{"cols":80,"rows":24}')]
    for cut in range(1, len(frames)):
        parser = FrameParser()
        assert parser.feed(frames[:cut]) + parser.feed(frames[cut:]) == expected
        parser.feed_eof()


def test_frame_parser_handles_coalesced_frames_and_rejects_truncated_eof() -> None:
    parser = FrameParser()
    payload = encode_frame(IN, b"a") + encode_frame(IN, b"bc") + encode_json_frame(BYE, {"reason": "done"})
    assert parser.feed(payload) == [(IN, b"a"), (IN, b"bc"), (BYE, b'{"reason":"done"}')]
    parser.feed_eof()

    for truncated in (b"\x02", encode_frame(IN, b"abc")[:-1]):
        parser = FrameParser()
        parser.feed(truncated)
        with pytest.raises(ProtocolError, match="truncated"):
            parser.feed_eof()


@pytest.fixture
def web_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    projects = tmp_path / "projects"
    project, request = write_project(projects)
    # Darwin caps AF_UNIX paths at roughly 104 bytes; pytest's descriptive
    # tmp_path is intentionally much longer, so keep this private root short.
    gates = Path(tempfile.mkdtemp(prefix="om-gates-", dir="/tmp"))
    owner = DisposableResources()
    signer = fake_signer(tmp_path)
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(gates))
    monkeypatch.setenv("OPENMONTAGE_PROJECTS_DIR", str(projects))
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", os.path.normcase(str(projects.resolve())))
    monkeypatch.setattr(server_mod, "_summary_cache", {})

    async def no_watch() -> None:
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    make_app = lambda: server_mod.create_app(port=PORT, capability_token=TOKEN)
    app = make_app()
    try:
        yield {
            "projects": projects,
            "project": project,
            "request": request,
            "gates": gates,
            "app": app,
            "make_app": make_app,
            "signer": signer,
            "owner": owner,
        }
    finally:
        try:
            owner.cleanup()
        finally:
            shutil.rmtree(gates, ignore_errors=True)


def _install_real_fake_spawn(monkeypatch: pytest.MonkeyPatch, world: dict[str, Any]) -> None:
    """Route production spawn calls through a real broker with one fake signer child."""

    def spawn(registry: TTYRegistry, project_dir: Path, request_id: str) -> subprocess.Popen[bytes]:
        assert project_dir == world["project"] and request_id == REQUEST
        process = launch_fake_broker(
            world["projects"],
            world["gates"],
            world["signer"],
            owner=world["owner"],
            wait_ready=False,
        )
        world.setdefault("spawned", []).append(process)
        registry.track_broker(process)
        return process

    monkeypatch.setattr(tty, "_spawn_broker", spawn)


class _BoundedWebSocket:
    """Own one TestClient receive worker and expose timeout-bounded results."""

    def __init__(self, client: TestClient, url: str, *, headers: dict[str, str], timeout: float = 8.0) -> None:
        self._context = client.websocket_connect(url, headers=headers)
        self._timeout = timeout
        self._results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stopped = threading.Event()
        self._websocket = None
        self._worker: threading.Thread | None = None

    def __enter__(self) -> "_BoundedWebSocket":
        entered = False
        try:
            self._websocket = self._context.__enter__()
            entered = True
            self._worker = threading.Thread(
                target=self._receive_forever,
                name="test-backlot-ws-receiver",
                daemon=True,
            )
            self._worker.start()
            return self
        except BaseException:
            self._stopped.set()
            if entered:
                self._context.__exit__(None, None, None)
            if self._worker is not None:
                self._worker.join(timeout=3)
            raise

    def _receive_forever(self) -> None:
        try:
            while not self._stopped.is_set():
                message = self._websocket.receive()
                self._results.put(("message", message))
                if message.get("type") == "websocket.close":
                    return
        except BaseException as exc:
            self._results.put(("error", exc))
        finally:
            self._stopped.set()

    def receive(self, *, timeout: float | None = None) -> dict[str, Any]:
        try:
            kind, value = self._results.get(timeout=self._timeout if timeout is None else timeout)
        except queue.Empty as exc:
            raise AssertionError("timed out waiting for TestClient WebSocket message") from exc
        if kind == "error":
            raise AssertionError(f"TestClient WebSocket receive failed: {value!r}") from value
        return value

    def receive_json(self, *, timeout: float | None = None) -> Any:
        message = self.receive(timeout=timeout)
        assert message.get("text") is not None, message
        return json.loads(message["text"])

    def send_json(self, value: Any) -> None:
        self._websocket.send_json(value)

    def send_text(self, value: str) -> None:
        self._websocket.send_text(value)

    def send_bytes(self, value: bytes) -> None:
        self._websocket.send_bytes(value)

    def close(self, code: int = 1000) -> None:
        self._websocket.close(code=code)

    def __exit__(self, exc_type, exc, traceback) -> bool | None:
        context_error: BaseException | None = None
        result: bool | None = None
        try:
            result = self._context.__exit__(exc_type, exc, traceback)
        except BaseException as caught:
            context_error = caught
        finally:
            self._stopped.set()
            if self._worker is not None:
                self._worker.join(timeout=3)
        if self._worker is not None and self._worker.is_alive():
            raise AssertionError("TestClient WebSocket receive worker did not stop after context close")
        if context_error is not None:
            raise context_error
        return result


def _bounded_websocket(
    client: TestClient,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float = 8.0,
) -> _BoundedWebSocket:
    return _BoundedWebSocket(client, url, headers=headers, timeout=timeout)


def _receive_route_output(
    websocket: _BoundedWebSocket,
    marker: bytes,
    *,
    timeout: float = 5.0,
) -> tuple[bytes, list[dict[str, Any]]]:
    output = bytearray()
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    while marker not in output:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for WebSocket output marker {marker!r}")
        message = websocket.receive(timeout=remaining)
        assert message["type"] != "websocket.close", message
        if message.get("bytes") is not None:
            output.extend(message["bytes"])
        elif message.get("text") is not None:
            events.append(json.loads(message["text"]))
    return bytes(output), events


def _receive_route_bye(
    websocket: _BoundedWebSocket,
    *,
    timeout: float = 5.0,
) -> tuple[bytes, list[dict[str, Any]]]:
    output = bytearray()
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    while not any(event.get("type") == "bye" for event in events):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("timed out waiting for WebSocket bye event")
        message = websocket.receive(timeout=remaining)
        assert message["type"] != "websocket.close", message
        if message.get("bytes") is not None:
            output.extend(message["bytes"])
        elif message.get("text") is not None:
            events.append(json.loads(message["text"]))
    return bytes(output), events


def _closed(client: TestClient, url: str, *, origin: str | None = ORIGIN, first: tuple[str, Any] | None = None) -> tuple[int, str]:
    headers = {"origin": origin} if origin is not None else {}
    with _bounded_websocket(client, url, headers=headers) as websocket:
        if first is not None:
            kind, value = first
            if kind == "json":
                websocket.send_json(value)
            elif kind == "text":
                websocket.send_text(value)
            else:
                websocket.send_bytes(value)
        message = websocket.receive()
        assert message["type"] == "websocket.close"
    return int(message["code"]), str(message.get("reason") or "")


@pytest.mark.parametrize(
    "first",
    [("json", {}), ("json", {"k": "wrong"}), ("text", "not-json"), ("binary", b"missing")],
    ids=["missing", "wrong", "malformed", "binary"],
)
def test_missing_wrong_or_malformed_token_closes_4401(web_world, first) -> None:
    with TestClient(web_world["app"]) as client:
        code, reason = _closed(client, f"/api/project/{PROJECT}/gate/{REQUEST}/tty", first=first)
    assert (code, reason) == (4401, "authentication required")


@pytest.mark.parametrize("origin", [None, "http://attacker.invalid", "null"])
def test_bad_or_missing_origin_is_refused_before_authentication(web_world, origin: str | None) -> None:
    with TestClient(web_world["app"]) as client:
        code, reason = _closed(client, f"/api/project/{PROJECT}/gate/{REQUEST}/tty", origin=origin)
    assert (code, reason) == (4403, "origin not allowed")


def test_bounded_websocket_timeout_closes_context_and_joins_receive_worker(web_world) -> None:
    async def hanging_route(websocket: WebSocket) -> None:
        await websocket.accept()
        await asyncio.Event().wait()

    web_world["app"].websocket("/test-only/hanging-websocket")(hanging_route)
    session: _BoundedWebSocket | None = None
    with TestClient(web_world["app"]) as client:
        session = _bounded_websocket(
            client,
            "/test-only/hanging-websocket",
            headers={"origin": ORIGIN},
            timeout=0.05,
        )
        with pytest.raises(AssertionError, match="timed out waiting"):
            with session as websocket:
                websocket.receive()

    assert session._worker is not None and not session._worker.is_alive()


def test_authenticated_fastapi_websocket_route_reaches_real_broker_and_pty(
    web_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_real_fake_spawn(monkeypatch, web_world)
    url = f"/api/project/{PROJECT}/gate/{REQUEST}/tty"
    with TestClient(web_world["app"]) as client:
        with _bounded_websocket(client, url, headers={"origin": ORIGIN}) as websocket:
            websocket.send_json({"k": TOKEN})
            refreshed = websocket.receive_json()
            assert refreshed["type"] == "packet_refreshed"
            initial, events = _receive_route_output(websocket, b"TTY_OK")
            assert events == [{"type": "input_ready"}]
            assert web_world["spawned"][0] in web_world["app"].state.tty_registry.spawned
            websocket.send_bytes(b"y")
            terminal, events = _receive_route_bye(websocket)

    process = web_world["spawned"][0]
    broker_log = process.communicate(timeout=8)[0]
    observed = initial + terminal + broker_log
    assert events[-1] == {"type": "bye", "reason": "exited", "exit_code": 0}
    assert b"HUMAN_BYTE:y" in observed
    assert b"UNEXPECTED_BYTE" not in observed and b"UNEXPECTED_SIGNAL" not in observed
    assert process.returncode == 0


def test_websocket_close_detaches_without_signaling_or_writing_to_live_signer(
    web_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_real_fake_spawn(monkeypatch, web_world)
    url = f"/api/project/{PROJECT}/gate/{REQUEST}/tty"
    with TestClient(web_world["app"]) as client:
        with _bounded_websocket(client, url, headers={"origin": ORIGIN}) as websocket:
            websocket.send_json({"k": TOKEN})
            assert websocket.receive_json()["type"] == "packet_refreshed"
            initial, _ = _receive_route_output(websocket, b"TTY_OK")
            session_id = json.loads(
                (web_world["gates"] / "sessions" / f"{PROJECT}.session.json").read_text(encoding="utf-8")
            )["session_id"]
            websocket.close()
            wait_until(
                lambda: PROJECT not in web_world["app"].state.tty_registry.sessions,
                timeout=3,
                description="WebSocket route reservation release",
            )

        process = web_world["spawned"][0]
        assert process.poll() is None
        raw, hello = attach_when_free(web_world["gates"] / "sessions" / f"{PROJECT}.sock")
        assert hello["session_id"] == session_id
        frame_type, replay = recv_frame(raw)
        assert frame_type == OUT and b"TTY_OK" in replay
        raw.sendall(encode_frame(IN, b"y"))
        final, events = collect_until_bye(raw)
        raw.close()

    broker_log = process.communicate(timeout=8)[0]
    observed = initial + replay + b"".join(final) + broker_log
    assert events[-1].get("exit_code") == 0
    assert b"UNEXPECTED_BYTE" not in observed and b"UNEXPECTED_SIGNAL" not in observed
    assert process.returncode == 0


def test_real_app_shutdown_detaches_and_fresh_app_route_reconnects_same_session_with_replay(
    web_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_real_fake_spawn(monkeypatch, web_world)
    url = f"/api/project/{PROJECT}/gate/{REQUEST}/tty"
    client_context = TestClient(web_world["app"])
    connected = threading.Event()
    closed = threading.Event()
    worker_result: dict[str, Any] = {}
    client_open = False
    worker: threading.Thread | None = None

    def hold_route_open() -> None:
        try:
            with _bounded_websocket(client, url, headers={"origin": ORIGIN}) as websocket:
                websocket.send_json({"k": TOKEN})
                worker_result["packet"] = websocket.receive_json()
                worker_result["initial"], _ = _receive_route_output(websocket, b"TTY_OK")
                connected.set()
                close_deadline = time.monotonic() + 5.0
                while True:
                    remaining = close_deadline - time.monotonic()
                    if remaining <= 0:
                        raise AssertionError("timed out waiting for app-shutdown WebSocket close")
                    message = websocket.receive(timeout=remaining)
                    if message["type"] == "websocket.close":
                        worker_result["close"] = message
                        return
        except BaseException as exc:
            worker_result["error"] = repr(exc)
        finally:
            connected.set()
            closed.set()

    try:
        client = client_context.__enter__()
        client_open = True
        worker = threading.Thread(target=hold_route_open, name="test-backlot-live-route", daemon=True)
        worker.start()
        assert connected.wait(timeout=5), worker_result
        assert "error" not in worker_result, worker_result
        process = web_world["spawned"][0]
        sidecar = web_world["gates"] / "sessions" / f"{PROJECT}.session.json"
        session_id = json.loads(sidecar.read_text(encoding="utf-8"))["session_id"]
        assert process.poll() is None

        # Exiting TestClient drives FastAPI's real lifespan shutdown while the
        # route is live; the registry must close only its broker client.
        client_context.__exit__(None, None, None)
        client_open = False
        assert closed.wait(timeout=5), worker_result
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert "error" not in worker_result, worker_result
        assert worker_result["close"] == {
            "type": "websocket.close",
            "code": 1012,
            "reason": "server shutting down",
        }
        assert process.poll() is None

        restarted_app = web_world["make_app"]()
        with TestClient(restarted_app) as restarted:
            with _bounded_websocket(restarted, url, headers={"origin": ORIGIN}) as websocket:
                websocket.send_json({"k": TOKEN})
                assert websocket.receive_json()["type"] == "packet_refreshed"
                replay, _ = _receive_route_output(websocket, b"TTY_OK")
                assert json.loads(sidecar.read_text(encoding="utf-8"))["session_id"] == session_id
                websocket.send_bytes(b"y")
                terminal, events = _receive_route_bye(websocket)
    finally:
        if client_open:
            client_context.__exit__(None, None, None)
        if worker is not None:
            worker.join(timeout=5)
            if worker.is_alive():
                raise AssertionError("live-route worker did not stop after TestClient cleanup")

    assert len(web_world["spawned"]) == 1
    broker_log = process.communicate(timeout=8)[0]
    observed = worker_result["initial"] + replay + terminal + broker_log
    assert events[-1] == {"type": "bye", "reason": "exited", "exit_code": 0}
    assert b"UNEXPECTED_BYTE" not in observed and b"UNEXPECTED_SIGNAL" not in observed
    assert process.returncode == 0


@pytest.mark.parametrize(
    ("variant", "expected_code"),
    [
        ("unknown-project", 4404),
        ("unknown-request", 4404),
        ("done", 4400),
        ("declined", 4400),
        ("abandoned", 4400),
        ("malformed", 4400),
        ("unknown-kind", 4400),
        ("malformed-approval", 4400),
        ("conflict", 4400),
        ("symlink", 4400),
        ("cross-project", 4400),
    ],
)
def test_unsafe_or_nonpending_requests_are_refused_before_spawn(
    web_world, monkeypatch: pytest.MonkeyPatch, variant: str, expected_code: int
) -> None:
    def spawn_forbidden(*_args, **_kwargs):
        raise AssertionError(f"unsafe {variant} request reached broker spawn")

    monkeypatch.setattr(tty, "_spawn_broker", spawn_forbidden)
    project = web_world["project"]
    pending = project / ".gate-requests" / f"{REQUEST}.json"
    url_project = PROJECT
    if variant == "unknown-project":
        url_project = "unknown"
    elif variant == "unknown-request":
        pending.rename(pending.with_name("other-request.json"))
    elif variant in {"done", "declined", "abandoned"}:
        nonpending = pending.parent / variant / pending.name
        nonpending.parent.mkdir(exist_ok=True)
        pending.rename(nonpending)
    elif variant == "malformed":
        pending.write_text("{", encoding="utf-8")
    elif variant == "unknown-kind":
        value = json.loads(pending.read_text(encoding="utf-8"))
        value["kind"] = "not-a-kind"
        pending.write_text(json.dumps(value), encoding="utf-8")
    elif variant == "malformed-approval":
        value = json.loads(pending.read_text(encoding="utf-8"))
        value["approval_record"] = "not-an-object"
        pending.write_text(json.dumps(value), encoding="utf-8")
    elif variant == "conflict":
        done = pending.parent / "done" / pending.name
        done.parent.mkdir()
        done.write_bytes(pending.read_bytes())
    elif variant == "symlink":
        outside = project.parent / "outside.json"
        outside.write_bytes(pending.read_bytes())
        pending.unlink()
        pending.symlink_to(outside)
    elif variant == "cross-project":
        value = json.loads(pending.read_text(encoding="utf-8"))
        value["project_id"] = "other"
        pending.write_text(json.dumps(value), encoding="utf-8")

    with TestClient(web_world["app"]) as client:
        code, _ = _closed(
            client,
            f"/api/project/{url_project}/gate/{REQUEST}/tty",
            first=("json", {"k": TOKEN}),
        )
    assert code == expected_code
    assert not list(web_world["gates"].glob("sessions/*.sock"))


def test_live_run_lease_closes_4423_without_spawning(web_world, monkeypatch: pytest.MonkeyPatch) -> None:
    def spawn_forbidden(*_args, **_kwargs):
        raise AssertionError("an unrelated live run lease reached broker spawn")

    monkeypatch.setattr(tty, "_spawn_broker", spawn_forbidden)
    with run_lease.acquire(web_world["project"], 10):
        assert tty._live_run_lease(web_world["project"])
        with TestClient(web_world["app"]) as client:
            code, reason = _closed(
                client,
                f"/api/project/{PROJECT}/gate/{REQUEST}/tty",
                first=("json", {"k": TOKEN}),
            )
    assert (code, reason) == (4423, "a run holds the project lease")
    assert not list(web_world["gates"].glob("sessions/*.sock"))


def test_second_in_process_session_closes_4409_with_required_reason(web_world) -> None:
    registry = web_world["app"].state.tty_registry
    with TestClient(web_world["app"]) as client:
        registry.sessions[PROJECT] = object()
        try:
            code, reason = _closed(
                client,
                f"/api/project/{PROJECT}/gate/{REQUEST}/tty",
                first=("json", {"k": TOKEN}),
            )
        finally:
            registry.sessions.pop(PROJECT, None)
    assert (code, reason) == (4409, "a signing terminal is already open for this project")


class _FakeWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _FakeWebSocket:
    def __init__(self, app, messages: list[dict[str, Any]] | None = None) -> None:
        self.scope = {"app": app}
        self.headers = {"origin": ORIGIN}
        self.messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for message in messages or []:
            self.messages.put_nowait(message)
        self.sent_json: list[dict[str, Any]] = []
        self.sent_bytes: list[bytes] = []
        self.closed: list[tuple[int, str]] = []

    async def accept(self) -> None:
        return None

    async def receive(self) -> dict[str, Any]:
        return await self.messages.get()

    async def send_json(self, value: dict[str, Any]) -> None:
        self.sent_json.append(value)

    async def send_bytes(self, value: bytes) -> None:
        self.sent_bytes.append(value)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def test_two_websocket_clients_racing_to_start_one_broker_yield_one_4409(web_world, monkeypatch) -> None:
    async def scenario() -> tuple[_FakeWebSocket, _FakeWebSocket]:
        release = asyncio.Event()
        writer = _FakeWriter()

        async def delayed_attach(project: str, request_id: str):
            await release.wait()
            reader = asyncio.StreamReader()
            return reader, writer, {
                "project": PROJECT, "request_id": REQUEST, "session_id": "race-session",
                "pid": 123, "lease_held": True, "signer": "running",
            }

        async def no_relay(*args) -> None:
            return None

        monkeypatch.setattr(tty, "_attach", delayed_attach)
        monkeypatch.setattr(tty, "_relay", no_relay)
        messages = [{"type": "websocket.receive", "text": json.dumps({"k": TOKEN})}]
        first = _FakeWebSocket(web_world["app"], messages)
        second = _FakeWebSocket(web_world["app"], messages)
        first_task = asyncio.create_task(tty._tty_websocket(first, PROJECT, REQUEST))
        await asyncio.sleep(0)
        second_task = asyncio.create_task(tty._tty_websocket(second, PROJECT, REQUEST))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first_task, second_task)
        return first, second

    first, second = asyncio.run(scenario())
    closings = first.closed + second.closed
    assert closings.count((4409, "a signing terminal is already open for this project")) == 1
    events = first.sent_json + second.sent_json
    assert [event["type"] for event in events] == ["packet_refreshed", "input_ready"]


def test_two_real_apps_racing_websocket_routes_yield_one_usable_session_and_one_4409(
    web_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_real_fake_spawn(monkeypatch, web_world)
    apps = [web_world["make_app"](), web_world["make_app"]()]
    contexts: list[TestClient] = []
    clients: list[TestClient] = []
    workers: list[threading.Thread] = []
    url = f"/api/project/{PROJECT}/gate/{REQUEST}/tty"
    start = threading.Barrier(3)
    finish_winner = threading.Event()
    outcomes: dict[int, dict[str, Any]] = {}
    outcome_lock = threading.Lock()

    def record(index: int, value: dict[str, Any]) -> None:
        with outcome_lock:
            outcomes[index] = value

    def race_route(index: int) -> None:
        output = bytearray()
        events: list[dict[str, Any]] = []
        try:
            with _bounded_websocket(clients[index], url, headers={"origin": ORIGIN}) as websocket:
                start.wait(timeout=5)
                websocket.send_json({"k": TOKEN})
                outcome_deadline = time.monotonic() + 5.0
                while b"TTY_OK" not in output:
                    remaining = outcome_deadline - time.monotonic()
                    if remaining <= 0:
                        raise AssertionError("timed out waiting for race route outcome")
                    message = websocket.receive(timeout=remaining)
                    if message["type"] == "websocket.close":
                        record(index, {"kind": "closed", **message})
                        return
                    if message.get("bytes") is not None:
                        output.extend(message["bytes"])
                    elif message.get("text") is not None:
                        events.append(json.loads(message["text"]))
                record(index, {"kind": "usable", "output": bytes(output), "events": events})
                if not finish_winner.wait(timeout=5):
                    raise AssertionError("winner was not released for explicit input")
                websocket.send_bytes(b"y")
                terminal, final_events = _receive_route_bye(websocket)
                record(index, {
                    "kind": "usable",
                    "output": bytes(output) + terminal,
                    "events": events + final_events,
                })
        except BaseException as exc:  # surfaced in the main test thread below
            record(index, {"kind": "error", "error": repr(exc)})

    try:
        for app in apps:
            context = TestClient(app)
            client = context.__enter__()
            contexts.append(context)
            clients.append(client)
        workers = [
            threading.Thread(target=race_route, args=(index,), name=f"test-backlot-racer-{index}", daemon=True)
            for index in range(2)
        ]
        for worker in workers:
            worker.start()
        start.wait(timeout=5)
        wait_until(
            lambda: len(outcomes) == 2,
            timeout=6,
            description="one usable route and one busy route",
        )
        snapshot = list(outcomes.values())
        assert sum(value["kind"] == "usable" for value in snapshot) == 1, snapshot
        assert sum(value["kind"] == "closed" for value in snapshot) == 1, snapshot
        refused = next(value for value in snapshot if value["kind"] == "closed")
        assert (refused["code"], refused.get("reason")) == (
            4409,
            "a signing terminal is already open for this project",
        )
        finish_winner.set()
        for worker in workers:
            worker.join(timeout=8)
        assert not any(worker.is_alive() for worker in workers), outcomes
    finally:
        finish_winner.set()
        cleanup_errors: list[str] = []
        for context in reversed(contexts):
            try:
                context.__exit__(None, None, None)
            except BaseException as exc:
                cleanup_errors.append(f"TestClient close failed: {exc!r}")
        for worker in workers:
            worker.join(timeout=5)
            if worker.is_alive():
                cleanup_errors.append(f"worker {worker.name} did not stop")
        if cleanup_errors:
            raise AssertionError("; ".join(cleanup_errors))

    final = list(outcomes.values())
    assert not [value for value in final if value["kind"] == "error"], final
    usable = next(value for value in final if value["kind"] == "usable")
    assert usable["events"][-1] == {"type": "bye", "reason": "exited", "exit_code": 0}
    assert b"HUMAN_BYTE:y" in usable["output"]
    assert len(web_world["spawned"]) == 2
    logs = [process.communicate(timeout=8)[0] for process in web_world["spawned"]]
    assert sorted(process.returncode for process in web_world["spawned"]) == [0, 5]
    assert b"UNEXPECTED_BYTE" not in b"".join(logs) + usable["output"]
    assert b"UNEXPECTED_SIGNAL" not in b"".join(logs) + usable["output"]


def test_attach_rejects_real_socket_hello_sidecar_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gates = Path(tempfile.mkdtemp(prefix="om-gates-", dir="/tmp"))
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(gates))

    async def scenario() -> None:
        paths = tty.session_paths(PROJECT)
        paths["sidecar"].write_text(json.dumps({
            "project": PROJECT,
            "request_id": REQUEST,
            "session_id": "sidecar-session",
            "pid": os.getpid(),
            "started": "2026-08-30T00:00:00+00:00",
        }), encoding="utf-8")

        async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(encode_json_frame(HELLO, {
                "project": PROJECT,
                "request_id": REQUEST,
                "session_id": "different-session",
                "pid": os.getpid(),
                "lease_held": True,
                "signer": "running",
            }))
            await writer.drain()
            await reader.read()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(peer, path=str(paths["socket"]))
        try:
            with pytest.raises(IdentityMismatch, match="session identity mismatch"):
                await tty._attach(PROJECT, REQUEST)
        finally:
            server.close()
            await server.wait_closed()

    try:
        asyncio.run(scenario())
    finally:
        shutil.rmtree(gates, ignore_errors=True)


def test_websocket_maps_hello_sidecar_mismatch_to_required_reason(web_world, monkeypatch) -> None:
    async def mismatch(*args):
        raise IdentityMismatch("session identity mismatch")

    monkeypatch.setattr(tty, "_attach", mismatch)
    websocket = _FakeWebSocket(web_world["app"], [
        {"type": "websocket.receive", "text": json.dumps({"k": TOKEN})},
    ])
    asyncio.run(tty._tty_websocket(websocket, PROJECT, REQUEST))
    assert websocket.closed == [(4409, "session identity mismatch")]


def test_two_independent_registries_racing_broker_start_get_one_attach_and_one_busy(
    web_world, tmp_path: Path, monkeypatch
) -> None:
    signer = fake_signer(tmp_path)
    processes: list[subprocess.Popen[bytes]] = []

    def spawn(registry: TTYRegistry, project_dir: Path, request_id: str) -> subprocess.Popen[bytes]:
        env = process_env(web_world["projects"], web_world["gates"], signer)
        env["OM_BROKER"] = "1"
        process = subprocess.Popen(
            [sys.executable, "-c", _WRAPPER_BOOTSTRAP],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        web_world["owner"].track_process(
            process,
            expires_after=10.0,
            wrapper_last_resort=True,
            label="registry-race broker wrapper",
        )
        processes.append(process)
        registry.track_broker(process)
        return process

    async def scenario() -> list[Any]:
        monkeypatch.setattr(tty, "_spawn_broker", spawn)
        attempts = [
            tty._connect_or_spawn(TTYRegistry(), web_world["project"], PROJECT, REQUEST),
            tty._connect_or_spawn(TTYRegistry(), web_world["project"], PROJECT, REQUEST),
        ]
        outcomes = await asyncio.gather(*attempts, return_exceptions=True)
        attached = [value for value in outcomes if isinstance(value, tuple)]
        refused = [value for value in outcomes if isinstance(value, Exception)]
        assert len(attached) == 1
        assert len(refused) == 1 and isinstance(refused[0], tty.BrokerBusy)
        reader, writer, _hello = attached[0]
        terminal = bytearray()
        while b"TTY_OK" not in terminal:
            frame_type, payload = await read_frame(reader)
            assert frame_type == OUT
            terminal.extend(payload)
        writer.write(encode_frame(IN, b"y"))
        await writer.drain()
        while True:
            frame_type, payload = await read_frame(reader)
            if frame_type == BYE:
                assert decode_json_payload(BYE, payload)["exit_code"] == 0
                break
        writer.close()
        await writer.wait_closed()
        return outcomes

    asyncio.run(scenario())
    for process in processes:
        output = process.communicate(timeout=8)[0]
        assert process.returncode in (0, 5), output.decode(errors="replace")
    assert sorted(process.returncode for process in processes) == [0, 5]


def test_lease_held_attach_refetches_packet_and_drops_pre_refresh_binary(web_world, monkeypatch) -> None:
    initial = server_mod.load_gate_detail(web_world["project"], REQUEST)

    async def scenario() -> _FakeWebSocket:
        writer = _FakeWriter()

        async def unavailable(*args):
            raise BrokerUnavailable("not published yet")

        async def reconcile(project: str, request_id: str):
            pending = web_world["project"] / ".gate-requests" / f"{REQUEST}.json"
            value = json.loads(pending.read_text(encoding="utf-8"))
            value["summary"] = "Refreshed PROJECT configuration."
            pending.write_text(json.dumps(value), encoding="utf-8")
            await asyncio.sleep(0)
            return asyncio.StreamReader(), writer, {
                "project": PROJECT, "request_id": REQUEST, "session_id": "refresh-session",
                "pid": 123, "lease_held": True, "signer": "running",
            }

        async def verify_no_stale_input(_ws, _reader, _writer, incoming):
            await asyncio.sleep(0)
            queued = []
            while not incoming.empty():
                queued.append(incoming.get_nowait())
            assert not any(kind == "input" for kind, _ in queued)

        monkeypatch.setattr(tty, "_attach", unavailable)
        monkeypatch.setattr(tty, "_live_run_lease", lambda root: True)
        monkeypatch.setattr(tty, "_reconcile_live_lease", reconcile)
        monkeypatch.setattr(tty, "_relay", verify_no_stale_input)
        websocket = _FakeWebSocket(web_world["app"], [
            {"type": "websocket.receive", "text": json.dumps({"k": TOKEN})},
            {"type": "websocket.receive", "bytes": b"y"},
            {"type": "websocket.disconnect"},
        ])
        await tty._tty_websocket(websocket, PROJECT, REQUEST)
        return websocket

    websocket = asyncio.run(scenario())
    refreshed = next(event["packet"] for event in websocket.sent_json if event.get("type") == "packet_refreshed")
    assert refreshed != initial
    assert refreshed["packet"]["summary"] == "Refreshed PROJECT configuration."
    assert {event.get("type") for event in websocket.sent_json} >= {"packet_refreshed", "input_ready"}


def test_packet_error_refresh_never_advertises_input_ready_or_forwards_input(web_world, monkeypatch) -> None:
    async def scenario() -> tuple[_FakeWebSocket, _FakeWriter]:
        writer = _FakeWriter()

        async def attached(*args):
            return asyncio.StreamReader(), writer, {
                "project": PROJECT, "request_id": REQUEST, "session_id": "packet-error-session",
                "pid": 123, "lease_held": True, "signer": "running",
            }

        async def relay_forbidden(*args):
            raise AssertionError("packet-error evidence reached the input relay")

        monkeypatch.setattr(tty, "_attach", attached)
        monkeypatch.setattr(tty, "_relay", relay_forbidden)
        monkeypatch.setattr(
            server_mod,
            "load_gate_detail",
            lambda *_args: {
                "state": "pending",
                "request_id": REQUEST,
                "packet": {"packet_error": True, "error": "checkpoint exceeds byte budget"},
            },
        )
        websocket = _FakeWebSocket(web_world["app"], [
            {"type": "websocket.receive", "text": json.dumps({"k": TOKEN})},
            {"type": "websocket.receive", "bytes": b"y"},
            {"type": "websocket.disconnect"},
        ])
        await tty._tty_websocket(websocket, PROJECT, REQUEST)
        return websocket, writer

    websocket, writer = asyncio.run(scenario())

    assert [event["type"] for event in websocket.sent_json] == ["packet_refreshed"]
    assert writer.data == b""
    assert websocket.closed == [(4422, "gate evidence is unavailable")]


def test_websocket_binary_and_resize_control_forward_as_bounded_broker_frames() -> None:
    async def scenario() -> bytes:
        app = SimpleNamespace(state=SimpleNamespace())
        websocket = _FakeWebSocket(app, [
            {"type": "websocket.receive", "text": json.dumps({"type": "resize", "cols": 10, "rows": 3})},
            {"type": "websocket.receive", "text": json.dumps({"type": "resize", "cols": 9, "rows": 3})},
            {"type": "websocket.receive", "bytes": b"human"},
            {"type": "websocket.disconnect"},
        ])
        queue: asyncio.Queue = asyncio.Queue()
        ready = asyncio.Event()
        ready.set()
        writer = _FakeWriter()
        await tty._websocket_input(websocket, queue, ready)
        await tty._page_to_broker(queue, writer)
        return bytes(writer.data)

    raw = asyncio.run(scenario())
    parser = FrameParser()
    frames = parser.feed(raw)
    parser.feed_eof()
    assert frames == [(RESIZE, b'{"cols":10,"rows":3}'), (IN, b"human")]


def test_broker_output_round_trips_as_binary_and_status_json() -> None:
    async def scenario() -> tuple[list[bytes], list[dict[str, Any]]]:
        app = SimpleNamespace(state=SimpleNamespace())
        websocket = _FakeWebSocket(app)
        reader = asyncio.StreamReader()
        reader.feed_data(
            encode_frame(OUT, b"\x00terminal\xff")
            + encode_json_frame(STATUS, {"signer": "running"})
            + encode_json_frame(BYE, {"reason": "exited", "exit_code": 0})
        )
        reader.feed_eof()
        outgoing: asyncio.Queue = asyncio.Queue()
        condition = asyncio.Condition()
        pending = [0]
        producer = asyncio.create_task(tty._broker_to_page_queue(reader, outgoing, condition, pending))
        sender = asyncio.create_task(tty._page_sender(websocket, outgoing, condition, pending))
        await producer
        await outgoing.join()
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        return websocket.sent_bytes, websocket.sent_json

    binary, events = asyncio.run(scenario())
    assert binary == [b"\x00terminal\xff"]
    assert events == [
        {"type": "status", "signer": "running"},
        {"type": "bye", "reason": "exited", "exit_code": 0},
    ]


def test_output_backpressure_never_exceeds_configured_bound() -> None:
    async def scenario() -> tuple[int, int, bool]:
        reader = asyncio.StreamReader()
        frame = encode_frame(OUT, b"x" * MAX_OUT)
        reader.feed_data(frame * 5)
        outgoing: asyncio.Queue = asyncio.Queue()
        condition = asyncio.Condition()
        pending = [0]
        task = asyncio.create_task(tty._broker_to_page_queue(reader, outgoing, condition, pending))
        deadline = asyncio.get_running_loop().time() + 1.0
        while pending[0] < MAX_WS_PENDING and not task.done():
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("producer did not reach the configured backpressure bound")
            await asyncio.sleep(0)
        result = (pending[0], outgoing.qsize(), task.done())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return result

    pending, queued, done = asyncio.run(scenario())
    assert pending == MAX_WS_PENDING == 4 * MAX_OUT
    assert queued == 4
    assert done is False


def test_server_shutdown_detaches_only_and_restarted_registry_attaches_same_broker_replay(
    web_world, tmp_path: Path
) -> None:
    signer = fake_signer(tmp_path)
    process = launch_fake_broker(
        web_world["projects"], web_world["gates"], signer, owner=web_world["owner"]
    )
    socket_path = web_world["gates"] / "sessions" / f"{PROJECT}.sock"

    class ClosingOnlyWebSocket:
        def __init__(self) -> None:
            self.closes: list[tuple[int, str]] = []

        async def close(self, code: int, reason: str) -> None:
            self.closes.append((code, reason))

    async def scenario() -> bytes:
        first_reader, first_writer, first_hello = await tty._attach(PROJECT, REQUEST)
        first_ws = ClosingOnlyWebSocket()
        old = TTYRegistry()
        relay = await old.reserve(PROJECT, first_ws)
        assert relay is not None
        relay.writer = first_writer
        await old.shutdown()
        assert first_ws.closes == [(1012, "server shutting down")]
        assert process.poll() is None

        deadline = asyncio.get_running_loop().time() + 2
        while True:
            try:
                second_reader, second_writer, second_hello = await tty._attach(PROJECT, REQUEST)
                break
            except tty.BrokerBusy:
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                await asyncio.sleep(0.02)
        assert second_hello["session_id"] == first_hello["session_id"]
        restarted = TTYRegistry()
        restarted_ws = ClosingOnlyWebSocket()
        restarted_relay = await restarted.reserve(PROJECT, restarted_ws)
        assert restarted_relay is not None
        restarted_relay.writer = second_writer
        frame_type, replay = await read_frame(second_reader)
        assert frame_type == OUT
        second_writer.write(encode_frame(IN, b"y"))
        await second_writer.drain()
        while True:
            frame_type, payload = await read_frame(second_reader)
            if frame_type == BYE:
                assert decode_json_payload(BYE, payload)["exit_code"] == 0
                break
        second_writer.close()
        await second_writer.wait_closed()
        await restarted.release(PROJECT, restarted_relay)
        return replay

    replay = asyncio.run(scenario())
    assert b"TTY_OK" in replay
    output = process.communicate(timeout=8)[0]
    assert process.returncode == 0, output.decode(errors="replace")
