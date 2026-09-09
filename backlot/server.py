"""Backlot server — FastAPI app: board state API, SSE change feed, media.

The watcher observes ``projects/`` with watchfiles; on any change it bumps a
per-project version and wakes SSE subscribers, who tell the browser to
refetch state. The server never writes to project directories.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import stat
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from backlot import DEFAULT_PORT
from backlot.state import (
    PROJECTS_DIR,
    REPO_ROOT,
    list_projects,
    load_board_state,
    load_gate_detail,
    summarize_project,
)

UI_DIR = Path(__file__).resolve().parent / "ui"
THUMB_CACHE_DIR = REPO_ROOT / ".backlot" / "thumbs"
THUMB_WIDTHS = (320, 640, 960)

# Paths inside a project whose changes are pure noise for the board.
_IGNORE_PARTS = {"node_modules", ".git", "__pycache__", ".cache"}

SSE_HEARTBEAT_SECONDS = 15


class BacklotApp(FastAPI):
    """FastAPI application with security headers at the outer ASGI boundary.

    FastAPI's user middleware is inside ``ServerErrorMiddleware``. Decorating
    the outgoing ASGI ``http.response.start`` frame instead means even a
    framework-generated 500 receives Backlot's mandatory browser policy.
    """

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return

        async def hardened_send(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                port = self.state.server_port
                headers.setdefault(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'self'; "
                    f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; "
                    # xterm's DOM renderer creates per-instance <style> rules
                    # for measured cell geometry, ANSI colors, and the cursor.
                    # Keep scripts strict-self; allow only inline *styles* so
                    # the pinned local runtime can render its required rules.
                    "style-src 'self' 'unsafe-inline'; script-src 'self'",
                )
                headers.setdefault("Referrer-Policy", "no-referrer")
            await send(message)

        await super().__call__(scope, receive, hardened_send)


def backlot_metadata_dir() -> Path:
    """Return the private per-user directory for Backlot runtime metadata."""
    directory = Path.home() / ".openmontage" / "backlot"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir's mode is filtered by umask and does not update an existing dir.
    directory.chmod(0o700)
    return directory


def server_log_path() -> Path:
    """The private lifecycle log shared by detached Backlot processes."""
    return backlot_metadata_dir() / "server.log"


def server_log(message: str) -> None:
    """Append a lifecycle event.

    This deliberately accepts only a pre-redacted message. Callers must never
    include capability tokens, terminal keystrokes, or terminal output.
    """
    try:
        with server_log_path().open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}\n")
    except OSError:
        # The board remains an observer: inability to log must not stop it.
        pass


def capability_token_path(port: int) -> Path:
    """Return the capability-token location for one local Backlot port."""
    return backlot_metadata_dir() / f"{port}.token"


def write_capability_token(port: int) -> str:
    """Atomically replace ``port``'s 32-byte, URL-safe capability token."""
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"invalid Backlot port: {port!r}")
    token = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    target = capability_token_path(port)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        payload = token.encode("ascii")
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("unable to write Backlot capability token")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return token


def read_capability_token(port: int) -> Optional[str]:
    """Read a previously-written local capability token without logging it."""
    try:
        token = capability_token_path(port).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    # A URL-safe encoding of exactly 32 random bytes is 43 characters without
    # padding. Reject partial/corrupt files rather than opening an unprotected
    # browser session.
    if len(token) != 43 or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in token):
        return None
    return token


def _ui_html(name: str, assets: tuple[str, ...]) -> HTMLResponse:
    html = (UI_DIR / name).read_text(encoding="utf-8")
    for asset in assets:
        path = UI_DIR / asset
        if path.is_file():
            version = str(int(path.stat().st_mtime))
            html = html.replace(f"/ui/{asset}", f"/ui/{asset}?v={version}")
    return HTMLResponse(html)


class ChangeHub:
    """Fan-out of project-change notifications to SSE subscribers.

    Subscriptions are filtered: a board subscribed to one project only ever
    receives that project's ids, so unrelated-project bursts can't flood its
    queue and starve out the one notification it actually needs.
    """

    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue, Optional[str]] = {}

    def subscribe(self, project_id: Optional[str] = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers[q] = project_id
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.pop(q, None)

    def publish(self, project_id: str) -> None:
        for q, only in list(self._subscribers.items()):
            if only is not None and only != project_id:
                continue
            try:
                q.put_nowait(project_id)
            except asyncio.QueueFull:
                # Queue holds only THIS subscriber's relevant ids, so a full
                # queue already guarantees a pending wake-up → safe to drop.
                pass


hub = ChangeHub()

# Library summaries are expensive to derive (full state parse per project);
# cache per project and invalidate from the watcher.
_summary_cache: dict[str, dict] = {}


def _invalidate_summary(project_id: str) -> None:
    _summary_cache.pop(project_id, None)


def _cached_summaries() -> list[dict]:
    if not PROJECTS_DIR.is_dir():
        return []
    summaries = []
    for entry in sorted(PROJECTS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        cached = _summary_cache.get(entry.name)
        if cached is None:
            try:
                cached = summarize_project(entry)
            except Exception:
                cached = {
                    "project_id": entry.name, "title": entry.name,
                    "pipeline_type": "unknown", "has_pipeline_state": False,
                    "poster": None, "live": False, "last_activity": 0,
                    "active_stage": None, "awaiting_human": False,
                    "stage_states": [], "completed_count": 0,
                    "render_count": 0, "scene_count": 0, "error": "unreadable",
                }
            _summary_cache[entry.name] = cached
        summaries.append(cached)
    summaries.sort(key=lambda s: (not s["live"], -(s["last_activity"] or 0)))
    return summaries


# Watch-loop hot path: pure string comparison, no per-path filesystem calls
# (change batches can be thousands of paths during a render).
_PROJECTS_ROOT_STR = os.path.normcase(str(PROJECTS_DIR.resolve()))


def _project_of_change(path_str: str) -> Optional[str]:
    """Map a changed filesystem path to a project id (None = irrelevant)."""
    norm = os.path.normcase(os.path.normpath(path_str))
    if not norm.startswith(_PROJECTS_ROOT_STR):
        return None
    rel = norm[len(_PROJECTS_ROOT_STR):].lstrip("\\/")
    if not rel:
        return None
    parts = rel.replace("\\", "/").split("/")
    if _IGNORE_PARTS.intersection(parts):
        return None
    return parts[0]


async def _watch_projects() -> None:
    """Background task: watch projects/ and publish debounced changes."""
    try:
        from watchfiles import awatch
    except ImportError:
        return  # watcher unavailable → board still works via manual refresh
    if not PROJECTS_DIR.is_dir():
        return
    async for changes in awatch(PROJECTS_DIR, recursive=True, step=400):
        touched: set[str] = set()
        for _change, path_str in changes:
            pid = _project_of_change(path_str)
            if pid:
                touched.add(pid)
        for pid in touched:
            _invalidate_summary(pid)
            hub.publish(pid)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Own and cleanly stop the project watcher with FastAPI's lifespan API."""

    task = asyncio.create_task(_watch_projects())
    app.state.watch_task = task
    try:
        yield
    finally:
        # A relay is only a client of the detached signer broker. Shutdown
        # closes those client sockets and reservations; it never touches the
        # PTY, child process, or broker-owned project lease.
        from backlot.tty import shutdown_tty

        await shutdown_tty(app)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app(*, port: Optional[int] = None, capability_token: Optional[str] = None) -> FastAPI:
    """Create Backlot's read-only HTTP application.

    ``cmd_serve`` supplies the port and its freshly generated capability token.
    They live in app state for Task 3's local WebSocket handshake, never in a
    response, URL query string, or log line.
    """
    app = BacklotApp(title="Backlot", docs_url=None, redoc_url=None, lifespan=_lifespan)
    app.state.server_port = port if port is not None else DEFAULT_PORT
    app.state.capability_token = capability_token
    app.state.worker_count = 1

    from backlot.tty import install_tty

    install_tty(app)

    # ---- API ----------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "app": "backlot"}

    @app.get("/api/projects")
    async def projects() -> list:
        return await asyncio.to_thread(_cached_summaries)

    @app.get("/api/project/{project_id}/state")
    async def project_state(project_id: str) -> dict:
        project_dir = _safe_project_dir(project_id)
        return await asyncio.to_thread(load_board_state, project_dir)

    @app.get("/api/project/{project_id}/gate/{request_id}")
    async def gate_detail(project_id: str, request_id: str) -> dict:
        """Return Task 1's lazy, read-only detail packet for one gate request."""
        from lib.run_common import RunError, validate_request_id

        project_dir = _safe_project_dir(project_id)
        try:
            validate_request_id(request_id)
        except RunError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        detail = await asyncio.to_thread(load_gate_detail, project_dir, request_id)
        state_name = detail.get("state")
        if state_name == "missing":
            if _regular_gate_request_exists(project_dir, request_id):
                raise HTTPException(status_code=409, detail="gate request state is invalid")
            raise HTTPException(status_code=404, detail="gate request not found")
        if state_name in {"conflict", "error"}:
            raise HTTPException(status_code=409, detail="gate request state is invalid")
        # The lazy builder only returns an error without a state for a bad
        # governed project/request. The project and id were already validated
        # above, so this is a corrupted request representation, not success.
        if detail.get("error") == "invalid governed project or request":
            raise HTTPException(status_code=409, detail="gate request state is invalid")
        return detail

    @app.get("/api/project/{project_id}/events")
    async def project_events(project_id: str, request: Request) -> StreamingResponse:
        _safe_project_dir(project_id)  # 404 early for unknown projects

        async def stream():
            q = hub.subscribe(project_id)
            try:
                yield _sse({"type": "hello", "project_id": project_id})
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield _sse({"type": "heartbeat", "ts": time.time()})
                        continue
                    # Coalesce bursts: drain anything else queued.
                    while not q.empty():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    yield _sse({"type": "change", "project_id": project_id})
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    @app.get("/api/library/events")
    async def library_events(request: Request) -> StreamingResponse:
        async def stream():
            q = hub.subscribe()
            try:
                yield _sse({"type": "hello"})
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        changed = await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield _sse({"type": "heartbeat", "ts": time.time()})
                        continue
                    while not q.empty():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    yield _sse({"type": "change", "project_id": changed})
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    # ---- Thumbnails (downscaled, cached on disk) ------------------------

    @app.get("/thumb/{project_id}/{file_path:path}")
    async def thumb(project_id: str, file_path: str, w: int = 640) -> FileResponse:
        project_dir = _safe_project_dir(project_id)
        target = (project_dir / file_path).resolve()
        try:
            target.relative_to(project_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="path escapes project")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="media not found")
        width = min(THUMB_WIDTHS, key=lambda x: abs(x - w))
        cached = await asyncio.to_thread(_thumbnail_for, target, width)
        if cached is None:
            # Never fall back to raw video bytes for an <img> consumer (F-03);
            # non-thumbable images are safe to serve as-is.
            if target.suffix.lower() in {".mp4", ".webm", ".mov"}:
                raise HTTPException(status_code=404, detail="no poster frame available")
            return FileResponse(target)
        return FileResponse(cached, media_type="image/jpeg")

    # ---- Media (range requests handled by FileResponse) ---------------

    @app.get("/media/{project_id}/{file_path:path}")
    async def media(project_id: str, file_path: str) -> FileResponse:
        project_dir = _safe_project_dir(project_id)
        target = (project_dir / file_path).resolve()
        try:
            target.relative_to(project_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="path escapes project")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="media not found")
        return FileResponse(target)

    # ---- UI ------------------------------------------------------------

    @app.get("/p/{project_id}")
    async def board_page(project_id: str) -> HTMLResponse:
        return _ui_html("board.html", ("board.css", "board.js"))

    @app.get("/p/{project_path:path}")
    async def board_page_path(project_path: str) -> HTMLResponse:
        return _ui_html("board.html", ("board.css", "board.js"))

    @app.get("/")
    async def library_page() -> HTMLResponse:
        return _ui_html("index.html", ("board.css", "library.js"))

    if UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    # The board is a long-lived SPA: a tab keeps running whatever board.js it
    # loaded, and browsers heuristically cache /ui assets. no-cache forces a
    # conditional revalidation (cheap 304 via ETag) on every load so UI fixes
    # show up on a plain refresh. Media/thumb responses keep normal caching.
    @app.middleware("http")
    async def ui_no_cache(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/ui") or path.startswith("/p/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    return app


def _safe_project_dir(project_id: str) -> Path:
    """Resolve a registered, non-symlinked project with stable HTTP errors."""
    from lib.run_common import ENTITY_ID_RE, RunError, resolve_project_root

    if not isinstance(project_id, str) or not ENTITY_ID_RE.fullmatch(project_id):
        raise HTTPException(status_code=400, detail="invalid project id")
    try:
        return resolve_project_root(project_id, projects_dir=PROJECTS_DIR)
    except RunError as exc:
        # Phase 0 projects predate project.yaml. Their project.json marker is
        # still the board's registration record, so retain that read-only
        # compatibility path while applying the resolver's confinement and
        # no-symlink rules. New governed projects always take the strict path.
        project_dir = PROJECTS_DIR / project_id
        marker = project_dir / "project.json"
        try:
            project_real = project_dir.resolve()
            project_real.relative_to(PROJECTS_DIR.resolve())
        except (OSError, ValueError):
            project_real = None
        if (
            project_real is not None
            and not project_dir.is_symlink()
            and project_dir.is_dir()
            and not marker.is_symlink()
            and marker.is_file()
        ):
            return project_real
        # A syntactically valid but missing, unregistered, or symlinked
        # project is intentionally indistinguishable from an unknown project.
        raise HTTPException(status_code=404, detail=f"unknown project: {project_id}") from exc


def _regular_gate_request_exists(project_dir: Path, request_id: str) -> bool:
    """Whether a safe, regular request file exists in any known gate state.

    Task 1 intentionally omits malformed or identity-invalid request files
    from its public rows. The route distinguishes those corrupt existing files
    (409) from a genuinely absent request (404) without resolving or opening
    any symlinked path. Unsafe tree components are reported by the Task 1
    builder as ``state == 'error'`` before this helper is consulted.
    """
    root = project_dir / ".gate-requests"
    try:
        root_info = root.lstat()
    except OSError:
        return False
    if not stat.S_ISDIR(root_info.st_mode):
        return False
    for relative in (Path("."), Path("done"), Path("declined"), Path("abandoned")):
        directory = root / relative
        try:
            directory_info = directory.lstat()
        except OSError:
            continue
        if not stat.S_ISDIR(directory_info.st_mode):
            continue
        try:
            request_info = (directory / f"{request_id}.json").lstat()
        except OSError:
            continue
        if stat.S_ISREG(request_info.st_mode):
            return True
    return False


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _thumbnail_for(source: Path, width: int) -> Optional[Path]:
    """Downscale an image (or extract a video poster frame) to a cached JPEG."""
    suffix = source.suffix.lower()
    is_image = suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    is_video = suffix in {".mp4", ".webm", ".mov"}
    if not (is_image or is_video):
        return None
    try:
        import hashlib
        stat = source.stat()
        key = hashlib.sha1(
            f"{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}".encode()
        ).hexdigest()[:20]
        cached = THUMB_CACHE_DIR / f"{key}.jpg"
        if cached.is_file():
            return cached
        THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Unique temp per request — concurrent misses for the same source
        # must not write (and replace from) the same temp file.
        import uuid
        tmp = THUMB_CACHE_DIR / f"{key}.{uuid.uuid4().hex[:8]}.tmp.jpg"
        if is_video:
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.5",
                 "-i", str(source), "-frames:v", "1",
                 "-vf", f"scale={width}:-2", str(tmp)],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0 or not tmp.is_file():
                return None
        else:
            from PIL import Image
            with Image.open(source) as img:
                img = img.convert("RGB")
                img.thumbnail((width, width * 3))
                img.save(tmp, "JPEG", quality=82)
        tmp.replace(cached)
        return cached
    except Exception:
        return None


app = create_app()
