"""Front Lot server side of the embedded Claude session (spec §3.3).

Spawns and attaches per-film brokers, relays their events to pages, and
reconciles orphaned sessions safely. Holds no conversation state of its own:
the broker owns the journal and the controller lease; the page owns its
cursor (the last event seq it applied).
"""
from __future__ import annotations

import asyncio, contextlib, fcntl, json, os, signal, subprocess, sys, threading, time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, WebSocket

from backlot import claude_frames as cf, tty
from scripts.claude_session import boot_time, paths as broker_paths, proc_started

REPO = Path(__file__).resolve().parents[1]
SPAWN_WAIT = 200        # first start may run the sandbox self-check (one model turn)
REPLACE_WAIT = 25       # End escalation is at most ~12 s; spec §3.1
OPEN_KINDS = frozenset({"start", "resume", "new", "attach"})
ACTION_KINDS = frozenset({"submit", "stop", "spend-decision", "take-control", "end", "new"})
TTY_FROM = 2 ** 62      # past the journal's end: the broker answers with a snapshot the tty socket ignores


def _log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"{stamp} [front-lot claude] {msg}", file=sys.stderr, flush=True)


def _started_matches(pid, recorded) -> bool:
    now = proc_started(pid) if pid else None
    return now is not None and recorded is not None and abs(now - float(recorded)) <= 1


def _same_boot(ident: dict) -> bool:
    return abs(boot_time() - float(ident.get("boot_time", 0))) < 2


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def broker_alive(slug: str) -> bool:
    """From the identity file only (never touches the broker's lock)."""
    try:
        ident = json.loads(broker_paths(slug)["ident"].read_text())
    except (OSError, ValueError):
        return False
    return _same_boot(ident) and _started_matches(ident.get("broker_pid"), ident.get("broker_started"))


@contextmanager
def _lifetime_lock_if_free(path: Path):
    """Yields True while holding the film's lifetime lock if no broker holds it; never waits."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    held = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); held = True
        except BlockingIOError:
            held = False
        yield held
    finally:
        if held:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _wait_released(slug: str, timeout: float) -> bool:
    """True once no broker holds the film's lifetime lock (a broker releases it last, after its files)."""
    end = time.time() + timeout
    while True:
        with _lifetime_lock_if_free(broker_paths(slug)["lock"]) as free:
            if free:
                return True
        if time.time() >= end:
            return False
        time.sleep(0.05)


def reconcile_orphans(slug: str) -> str:
    """Call only while holding the film's lifetime lock (so no broker owns the film)."""
    p = broker_paths(slug)

    def clear() -> None:
        for k in ("ident", "sock", "live"):
            p[k].unlink(missing_ok=True)

    try:
        ident = json.loads(p["ident"].read_text())
    except FileNotFoundError:
        clear()                                   # sockets with no identity belong to no one
        return "clean"
    except (OSError, ValueError):
        clear()
        return "stale-record"
    pid, pgid = ident.get("claude_pid"), ident.get("claude_pgid")
    if not pgid or not _same_boot(ident):
        clear()                                   # another boot: nothing recorded can still be ours
        return "stale-record"
    if not _group_alive(pgid):
        clear()
        return "clean"
    leader_now = proc_started(pid) if pid else None
    if leader_now is not None and not _started_matches(pid, ident.get("claude_started")):
        clear()                                   # the pid belongs to another program now: never signal it
        return "stale-record"
    if leader_now is None:
        return "stuck"                            # leader gone, group alive: ownership unprovable; keep the record
    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            pass                                  # macOS: a group holding only a zombie answers EPERM
        end = time.time() + 5
        while time.time() < end and _group_alive(pgid):
            # A leader that is our own child stays a zombie (and its group answers EPERM on macOS) until
            # reaped; reap it. Its start time was verified above, so it is the recorded process.
            with contextlib.suppress(ChildProcessError, OSError):
                os.waitpid(pid, os.WNOHANG)
            time.sleep(0.2)
        if not _group_alive(pgid):
            break
    if _group_alive(pgid):
        return "stuck"                            # never replace while the owned group survives
    clear()
    return "terminated"


def session_state(slug: str) -> dict:
    p = broker_paths(slug)
    can_resume = p["session"].exists()
    if p["unavailable"].exists():
        try:
            info = json.loads(p["unavailable"].read_text())
        except (OSError, ValueError):
            info = {}
        out = {"state": "unavailable", "reason": info.get("reason", "unknown"), "can_resume": can_resume}
        if info.get("version"):
            out["version"] = info["version"]
        return out
    if broker_alive(slug):
        return {"state": "running", "can_resume": False}
    return {"state": "ended" if can_resume else "none", "can_resume": can_resume}


def spawn_broker(slug: str, mode: str) -> dict:
    """mode: "start" | "resume" | "new". Never ends a live broker: replacement is the broker's own `new`
    action, accepted only from the controlling page (Task 8), after which the requesting relay calls this.
    One spawner per film (spawn lock); the broker's lifetime lock is only probed, never held while waiting."""
    p = broker_paths(slug)
    with open(p["spawn_lock"], "a+") as spawn:
        fcntl.flock(spawn, fcntl.LOCK_EX)
        end = time.time() + (REPLACE_WAIT if mode == "new" else 0)
        while True:
            with _lifetime_lock_if_free(p["lock"]) as free:
                if free:
                    result = reconcile_orphans(slug)   # under the film's lock (spec §3.1)
                    break
            if mode != "new":
                return {"state": "running"}          # a broker holds the film (running or starting): attach
            if time.time() >= end:
                return {"state": "running", "notice": "The previous conversation is still closing. Try again in a moment."}
            time.sleep(0.2)
        if result != "clean":
            _log(f"reconcile {slug}: {result}")
        if result == "stuck":
            return {"state": "unavailable", "reason": "previous-still-running", "can_resume": p["session"].exists()}
        if mode == "new":
            p["session"].unlink(missing_ok=True)
            # The journal is per film: left in place, the new broker would reopen it and the page would
            # replay the old conversation and its cards. Keep one previous copy for the record.
            if p["events"].exists():
                os.replace(p["events"], p["events"].with_name(f"{slug}.events.prev.jsonl"))
        p["unavailable"].unlink(missing_ok=True)
        argv = [sys.executable, str(REPO / "scripts" / "claude_session.py"), "--broker", "--project", slug]
        if mode == "resume" and p["session"].exists():
            argv.append("--resume")
        proc = subprocess.Popen(argv, cwd=REPO, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Reap it when it exits: an unreaped broker stays a zombie that `ps` still reports as started,
        # so its identity record would read as a live broker.
        threading.Thread(target=proc.wait, daemon=True).start()
        end = time.time() + SPAWN_WAIT
        while time.time() < end:
            if p["sock"].exists():
                return {"state": "running"}
            if p["unavailable"].exists():
                return session_state(slug)
            time.sleep(0.1)
        return {"state": "unavailable", "reason": "no-start"}


# -- page side ----------------------------------------------------------------------------------------

class _PageOut:
    """Bounded send queue for one page socket; a page that cannot keep up is closed (it reconnects)."""

    def __init__(self, websocket: WebSocket):
        self.ws = websocket
        self.queue: asyncio.Queue[tuple[str, Any, int]] = asyncio.Queue()
        self.pending = 0
        self.dead = False
        self.task = asyncio.create_task(self._sender())

    def send(self, kind: str, payload: Any) -> None:
        if self.dead:
            return
        size = len(payload) if kind == "bytes" else len(json.dumps(payload))
        if self.pending + size > tty.MAX_WS_PENDING:
            _log(f"page socket too far behind ({self.pending + size} bytes queued): closing it")
            self.dead = True
            asyncio.ensure_future(tty._close(self.ws, 1013, "too far behind"))
            return
        self.pending += size
        self.queue.put_nowait((kind, payload, size))

    def json(self, obj: dict) -> None:
        self.send("json", obj)

    async def _sender(self) -> None:
        while True:
            kind, payload, size = await self.queue.get()
            try:
                if kind == "bytes":
                    await self.ws.send_bytes(payload)
                else:
                    await self.ws.send_json(payload)
            except Exception as exc:
                # The page's socket stays open but hears nothing more: log it, it is a silent stall.
                _log(f"page send failed, page hears nothing more until it reconnects: {exc!r}")
                self.dead = True
                return
            finally:
                self.pending -= size

    async def close(self) -> None:
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


class _Relay:
    """One per page socket: a client of the film's broker. `tty` relays carry terminal bytes only."""

    def __init__(self, slug: str, page: str, out: _PageOut, tty_mode: bool = False):
        self.slug, self.page, self.out, self.tty = slug, page, out, tty_mode
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.pump: Optional[asyncio.Task] = None
        self.ended = asyncio.Event()             # tty sockets close when their broker says goodbye

    @property
    def is_open(self) -> bool:
        return self.writer is not None and not self.writer.is_closing()

    async def open(self, subscribe_from: int) -> bool:
        p = broker_paths(self.slug)
        loop = asyncio.get_running_loop()
        end = loop.time() + SPAWN_WAIT
        while True:
            if p["sock"].exists():
                try:
                    reader, writer = await asyncio.open_unix_connection(str(p["sock"]))
                    break
                except (ConnectionError, FileNotFoundError, OSError):
                    if not broker_alive(self.slug):
                        return False             # a socket nobody serves
            if p["unavailable"].exists() or loop.time() >= end:
                return False
            await asyncio.sleep(0.1)
        writer.write(cf.encode_json(cf.HELLO, {"subscribe_from": subscribe_from, "controller": True,
                                               "page": self.page}))
        await writer.drain()
        self.reader, self.writer = reader, writer
        self.pump = asyncio.create_task(self._pump(reader))
        _log(f"relay attached {self.slug} page={self.page[:8]} tty={self.tty} from={subscribe_from}")
        return True

    async def _drop(self) -> None:
        w, self.writer, self.reader = self.writer, None, None
        if w is not None:
            w.close()
            with contextlib.suppress(Exception):
                await w.wait_closed()

    async def _pump(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                try:
                    t, payload = await cf.read_frame(reader)
                except (asyncio.IncompleteReadError, ConnectionError, cf.FrameError) as exc:
                    _log(f"relay lost the broker {self.slug} page={self.page[:8]} tty={self.tty}: {exc!r}")
                    await self._drop()
                    await self._bye({"reason": "broker-exited"})
                    return
                if t == cf.OUT:
                    if self.tty:
                        self.out.send("bytes", payload)
                    continue
                if t not in cf.JSON_TYPES:
                    continue
                try:
                    d = cf.decode_json(t, payload)
                except cf.FrameError:
                    continue
                if t == cf.BYE:
                    await self._drop()
                    if d.get("reason") == "new" and d.get("page") == self.page and not self.tty:
                        res = await asyncio.to_thread(spawn_broker, self.slug, "new")
                        await self.settle(res, 0)  # the page resets its model on the new addon-hello
                    else:
                        await self._bye(d)
                    return
                if self.tty:
                    continue
                if t == cf.STATUS:
                    _log(f"status to page={self.page[:8]} controller={d.get('controller')} state={d.get('state')}")
                    self.out.json({"type": "status", **d})
                elif t == cf.EVENT:
                    ev = d.get("event") or {}
                    if ev.get("kind") == "turn":
                        _log(f"turn {ev.get('phase')} to page={self.page[:8]} seq={d.get('seq')} dead={self.out.dead}")
                    self.out.json({"type": "event", "seq": d.get("seq"), "event": d.get("event")})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log(f"relay for {self.slug} failed: {exc!r}")
            await self._drop()

    async def _bye(self, d: dict) -> None:
        # Forward only once the broker has let go of the film, so the page's next GET or Pick back up
        # sees the session ended rather than a broker still on its way out.
        await asyncio.to_thread(_wait_released, self.slug, REPLACE_WAIT)
        self.out.json({"type": "bye", "reason": d.get("reason"), **({"page": d["page"]} if d.get("page") else {})})
        self.ended.set()

    async def request(self, kind: str, frm: int) -> None:
        """start / resume / new / attach, as the page's first message or any later one."""
        if self.is_open:
            if kind == "new":
                await self.action({"type": "new"})
            return
        res = None if kind == "attach" else await asyncio.to_thread(spawn_broker, self.slug, kind)
        await self.settle(res, frm)

    async def settle(self, res: Optional[dict], frm: int) -> None:
        st = res if res is not None else session_state(self.slug)
        if st.get("notice"):
            self.out.json({"type": "event", "seq": None, "event": {"kind": "notice", "plain": st["notice"]}})
        if st.get("state") == "unavailable":
            self.out.json({"type": "unavailable", "reason": st.get("reason", "unknown")})
            return
        if st.get("state") == "running" and await self.open(frm):
            return
        self.out.json({"type": "status", "controller": False, "state": session_state(self.slug)["state"],
                       "session_id": None})

    async def action(self, msg: dict) -> None:
        if not self.is_open:
            return
        try:
            self.writer.write(cf.encode_json(cf.ACTION, msg))
            await self.writer.drain()
        except (cf.FrameError, ConnectionError, OSError):
            pass

    async def send_frame(self, frame: bytes) -> None:
        if not self.is_open:
            return
        try:
            self.writer.write(frame)
            await self.writer.drain()
        except (ConnectionError, OSError):
            pass

    async def close(self) -> None:
        if self.pump is not None:
            self.pump.cancel()
            await asyncio.gather(self.pump, return_exceptions=True)
        await self._drop()


async def _handshake(websocket: WebSocket, project_id: str) -> Optional[str]:
    app = websocket.scope["app"]
    port = app.state.server_port
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    origin = websocket.headers.get("origin")
    await websocket.accept()
    if origin not in allowed_origins:
        await tty._close(websocket, 4403, "origin not allowed")
        return None
    if not await tty._first_token(websocket, app.state.capability_token):
        await tty._close(websocket, 4401, "authentication required")
        return None
    from backlot import server as server_module
    try:
        server_module._safe_project_dir(project_id)
    except Exception:
        await tty._close(websocket, 4404, "unknown project")
        return None
    return project_id


async def _receive_json(websocket: WebSocket) -> Optional[dict]:
    """Next text message as a dict; None when the socket closed. Non-JSON text and binary are skipped."""
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return None
        text = message.get("text")
        if text is None:
            continue
        try:
            value = json.loads(text)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value


def _cursor(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


async def _live_websocket(websocket: WebSocket, project_id: str) -> None:
    slug = await _handshake(websocket, project_id)
    if slug is None:
        return
    app = websocket.scope["app"]
    out = _PageOut(websocket)
    relay: Optional[_Relay] = None
    try:
        while True:
            msg = await _receive_json(websocket)
            if msg is None:
                return
            kind = msg.get("type")
            if relay is None:
                page = msg.get("page")
                if kind not in OPEN_KINDS or not isinstance(page, str) or not page:
                    continue
                relay = _Relay(slug, page, out)
                app.state.claude_relays.add(relay)
                _log(f"page socket open {slug} page={page[:8]} first={kind}")
            elif kind in ACTION_KINDS or kind in OPEN_KINDS:
                _log(f"page {kind} {slug} page={relay.page[:8]} relay_open={relay.is_open} dead={out.dead}")
            if kind in OPEN_KINDS:
                await relay.request(kind, _cursor(msg.get("from")))
            elif kind in ACTION_KINDS:
                await relay.action(msg)
    except Exception as exc:
        if not isinstance(exc, (ConnectionError, OSError)) and type(exc).__name__ != "WebSocketDisconnect":
            _log(f"live socket for {slug} failed: {exc!r}")
    finally:
        if relay is not None:
            _log(f"page socket closed {slug} page={relay.page[:8]}")
            app.state.claude_relays.discard(relay)
            await relay.close()
        await out.close()


async def _tty_websocket(websocket: WebSocket, project_id: str) -> None:
    slug = await _handshake(websocket, project_id)
    if slug is None:
        return
    app = websocket.scope["app"]
    out = _PageOut(websocket)
    relay: Optional[_Relay] = None
    reader_task: Optional[asyncio.Task] = None
    try:
        first = await _receive_json(websocket)
        page = first.get("page") if first else None
        if not isinstance(page, str) or not page:
            await tty._close(websocket, 4400, "page id required")
            return
        relay = _Relay(slug, page, out, tty_mode=True)
        app.state.claude_relays.add(relay)
        if not await relay.open(TTY_FROM):
            out.json({"type": "status", "controller": False, "state": session_state(slug)["state"],
                      "session_id": None})

        async def page_to_broker() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                data = message.get("bytes")
                if data is not None:
                    for i in range(0, len(data), cf.LIMITS[cf.IN]):
                        await relay.send_frame(cf.encode(cf.IN, data[i:i + cf.LIMITS[cf.IN]]))
                    continue
                try:
                    msg = json.loads(message.get("text") or "")
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "resize":
                    try:
                        frame = cf.encode_json(cf.RESIZE, {"cols": msg.get("cols"), "rows": msg.get("rows")})
                    except cf.FrameError:
                        continue
                    await relay.send_frame(frame)

        reader_task = asyncio.create_task(page_to_broker())
        ended = asyncio.create_task(relay.ended.wait())
        await asyncio.wait({reader_task, ended}, return_when=asyncio.FIRST_COMPLETED)
        ended.cancel()
        if relay.ended.is_set():
            await asyncio.sleep(0.05)            # let the bye reach the page before closing
            await tty._close(websocket, 1000, "session ended")
    except Exception as exc:
        if not isinstance(exc, (ConnectionError, OSError)) and type(exc).__name__ != "WebSocketDisconnect":
            _log(f"tty socket for {slug} failed: {exc!r}")
    finally:
        if reader_task is not None:
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
        if relay is not None:
            app.state.claude_relays.discard(relay)
            await relay.close()
        await out.close()


def install_claude(app: FastAPI) -> None:
    from fastapi import WebSocket as FastAPIWebSocket

    app.state.claude_relays = set()

    @app.get("/api/project/{project_id}/claude")
    async def claude_state(project_id: str) -> dict:
        from backlot import server as server_module

        server_module._safe_project_dir(project_id)
        return await asyncio.to_thread(session_state, project_id)

    async def claude_live(websocket: WebSocket, project_id: str) -> None:
        await _live_websocket(websocket, project_id)

    async def claude_tty(websocket: WebSocket, project_id: str) -> None:
        await _tty_websocket(websocket, project_id)

    claude_live.__annotations__["websocket"] = FastAPIWebSocket
    claude_tty.__annotations__["websocket"] = FastAPIWebSocket
    app.websocket("/api/project/{project_id}/claude/live")(claude_live)
    app.websocket("/api/project/{project_id}/claude/tty")(claude_tty)


async def shutdown_claude(app: FastAPI) -> None:
    """Closes every relay; brokers keep running (they survive a Front Lot restart)."""
    for relay in list(getattr(app.state, "claude_relays", ())):
        with contextlib.suppress(Exception):
            await relay.close()
    getattr(app.state, "claude_relays", set()).clear()
