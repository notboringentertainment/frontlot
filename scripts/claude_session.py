"""Per-film broker for Front Lot's embedded Claude (spec §3.1).

Owns: the claude PTY (raw terminal + 64 KB replay), the add-on live endpoint
(Story-drive protocol + /run), the request store, the event journal, and the
controller lease. Survives Front Lot restarts. Ends on End/New, SIGTERM, or
when Claude exits.
"""
from __future__ import annotations

import argparse, asyncio, collections, contextlib, fcntl, hashlib, json, os, pty, re, secrets, signal, struct, subprocess, sys, termios, time, uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from backlot import claude_frames as cf                       # noqa: E402
from backlot import claude_settings as cs                     # noqa: E402
from backlot.claude_journal import Journal                    # noqa: E402
from backlot.claude_ops import OpError, prepare               # noqa: E402
from backlot.claude_requests import Rejected, RequestStore    # noqa: E402
from backlot.tty import metadata_root, valid_resize           # noqa: E402
from lib.paths import PROJECTS_DIR                            # noqa: E402
from scripts.gate_sign import _login_environment, _safe_replay_tail  # noqa: E402

REPLAY_BYTES = 64 * 1024
LEASE_SECONDS = float(os.environ.get("FRONTLOT_LEASE_SECONDS", "30"))
NO_HELLO_SECONDS, SILENT_SECONDS = 8, 15          # Story-drive's add-on timeouts (spec §5)
STOP_NOTICE_SECONDS = float(os.environ.get("FRONTLOT_STOP_NOTICE_SECONDS", "15"))
REDELIVER_SECONDS = float(os.environ.get("FRONTLOT_REDELIVER_SECONDS", "10"))
WATCH_SECONDS = 2
POLL_SECONDS = 25
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RID_RE = re.compile(r"^r-[0-9a-f]{10}$")
STOPPED_THROUGH = re.compile(r"^stopped-through:(\d+)$")
READ_ONLY = "This window is read-only. Use Take control to act here."
STALE = "That request came from a part of the conversation that was stopped or restarted, so it was not run."
TOO_LONG = "Part of the conversation is too long to show here; it is in the terminal view."
LAUNCH_BROKE = "Front Lot couldn't start that run. Nothing was spent."
LAUNCH_UNSURE = "Front Lot isn't sure that run started. It will not be retried; Claude will be told what happened."
STARTED_UNSHOWN = "That run started, but this window couldn't be updated."
DECIDE_BROKE = "Front Lot couldn't record that answer. Try again."
NO_STOP_CONFIRM = "Claude hasn't confirmed it stopped — start a new conversation to be sure."
REPLY_STATUS = {"waiting-for-ben": "waiting-for-ben", "approved": "running", "launching": "running",
                "running": "running", "uncertain": "unknown-outcome", "done": "done", "failed": "failed",
                "declined": "declined", "cancelled": "cancelled", "expired": "expired"}
CARD_STATE = {"approved": "running", "launching": "running"}
OUTCOME_WORD = {"done": "finished", "failed": "failed", "uncertain": "not sure it ran — it will not be retried"}
NOTICE_WORD = {"done": "finished", "failed": "failed", "uncertain": "may or may not have run",
               "expired": "expired unanswered"}
INITIAL_SIZE = (40, 120)                          # rows, cols until a page sends its own size
MAX_CLIENT_BUFFER = 8 * 1024 * 1024               # a page this far behind is dropped (it reconnects)
MAX_PTY_PENDING = 64 * 1024


class Unavailable(Exception):
    def __init__(self, reason: str, version: str | None = None):
        super().__init__(reason)
        self.reason, self.version = reason, version


class _Aborted(Exception):
    """Startup noticed a shutdown already under way."""


def claude_dir() -> Path:
    d = metadata_root() / "claude"; d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def paths(slug: str) -> dict[str, Path]:
    d = claude_dir()
    return {k: d / f"{slug}{suffix}" for k, suffix in {
        "sock": ".sock", "lock": ".lock", "spawn_lock": ".spawn.lock", "ident": ".json", "live": ".live.sock",
        "events": ".events.jsonl", "settings": ".settings.json", "brief": ".brief.md", "session": ".session",
        "unavailable": ".unavailable.json", "sandbox_ok": ".sandbox-ok.json", "canary": ".canary"}.items()}


def boot_time() -> float:
    out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
    return float(out.split("sec =")[1].split(",")[0])   # "{ sec = 1791400000, usec = 0 } ..."


def proc_started(pid: int) -> float | None:
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if not out:
        return None
    return time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y"))


def resolve_claude() -> tuple[str, str]:
    pinned = os.environ.get("FRONTLOT_CLAUDE")   # when set, it is the only candidate
    cand = [pinned] if pinned else [str(Path.home() / ".local/bin/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    for c in cand:
        if not (Path(c).is_file() and os.access(c, os.X_OK)):
            continue
        if os.environ.get("FRONTLOT_SKIP_PREFLIGHT"):
            return c, "test"
        try:
            v = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
            nums = tuple(int(x) for x in v.split()[0].split(".")[:3])
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            raise Unavailable("missing") from None
        if nums < cs.MIN_LIVE_VERSION:
            raise Unavailable("old-version", v)
        auth = subprocess.run([c, "auth", "status", "--json"], capture_output=True, text=True, timeout=20)
        try:
            logged_in = json.loads(auth.stdout or "{}").get("loggedIn") is True
        except ValueError:
            logged_in = False
        if auth.returncode != 0 or not logged_in:
            raise Unavailable("signed-out", v)
        return c, v
    raise Unavailable("missing")


def canary_paths(slug: str, work: Path) -> tuple[Path, Path, Path]:
    """The self-check's deny canary (metadata root), allow canary (work area), and must-not-exist write target."""
    return paths(slug)["canary"], work / ".frontlot-canary", work.parent / ".frontlot-canary-write"


def sandbox_selfcheck(slug: str, claude: str, version: str, settings_file: Path, work: Path, env: dict,
                      on_spawn=None) -> bool:
    """Fail closed (spec §4.1): the session starts only if, by tool-level evidence, sandboxed Bash can read an
    allowed canary, is refused the one under the denied metadata root, and the Write tool is refused outside the
    work area (and nothing was written). One short Claude turn, cached per Claude version + settings."""
    p = paths(slug)
    want = {"version": version, "settings_sha256": hashlib.sha256(settings_file.read_bytes()).hexdigest()}
    try:
        if json.loads(p["sandbox_ok"].read_text()) == want:
            return True
    except (OSError, ValueError):
        pass
    deny_secret, allow_secret = secrets.token_hex(8), secrets.token_hex(8)
    _, allow_file, outside = canary_paths(slug, work)
    p["canary"].write_text(deny_secret); allow_file.write_text(allow_secret); outside.unlink(missing_ok=True)
    proc = None
    try:
        # Its own process group, and the handle goes to the broker (on_spawn), so a shutdown mid-check can kill
        # the whole check and remove the canaries even though this runs in an executor thread.
        proc = subprocess.Popen(cs.selfcheck_argv(claude=claude, settings_file=settings_file,
                                                  prompt=cs.selfcheck_prompt(p["canary"], allow_file, outside)),
                                cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        if on_spawn is not None:
            on_spawn(proc)
        stdout, _ = proc.communicate(timeout=180)
        passed = proc.returncode is not None and cs.selfcheck_passed(
            stdout, deny_file=p["canary"], allow_file=allow_file, outside_file=outside,
            deny_secret=deny_secret, allow_secret=allow_secret)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        passed = False
        if proc is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                proc.wait(5)
    finally:
        p["canary"].unlink(missing_ok=True); allow_file.unlink(missing_ok=True); outside.unlink(missing_ok=True)
    if passed:
        p["sandbox_ok"].write_text(json.dumps(want))
    return passed


def run_env() -> dict[str, str]:
    """Pipeline scripts run outside the sandbox as Ben, with his normal environment and keys."""
    return {k: v for k, v in os.environ.items()
            if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_") and not k.startswith("FRONTLOT_LIVE_")}


def write_private(path: Path, text: str) -> None:
    """Atomic, fsync'd, mode 0600."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, text.encode("utf-8")); os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


async def read_http(reader: asyncio.StreamReader) -> tuple[str, dict[str, str], dict]:
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    lines = head.decode("latin-1").split("\r\n")
    method, route, _ = lines[0].split(" ", 2)
    headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)}
    length = int(headers.get("content-length", "0"))
    if method != "POST" or length > 1024 * 1024:
        raise ValueError("bad request")
    raw = await asyncio.wait_for(reader.readexactly(length), 10) if length else b""
    body = json.loads(raw or b"{}")
    if not isinstance(body, dict):
        raise ValueError("bad request")
    return route, headers, body


def http_reply(writer: asyncio.StreamWriter, status: str, body: dict) -> None:
    raw = json.dumps(body).encode()
    writer.write(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\n"
                 f"Connection: close\r\n\r\n".encode() + raw)


def reply_for(rec: dict) -> dict:
    return {"requestId": rec["id"], "status": REPLY_STATUS[rec["state"]], "plain": rec["summary"]}


def card(rec: dict) -> dict:
    return {"requestId": rec["id"], "summary": rec["summary"], "entity": rec.get("entity"),
            "estimate_usd": rec.get("estimate_usd"), "paid": rec["paid"],
            "state": CARD_STATE.get(rec["state"], rec["state"])}


def _log(msg: str) -> None:
    with contextlib.suppress(Exception):
        sys.stderr.write(f"[claude_session {time.strftime('%H:%M:%S')}] {msg}\n"); sys.stderr.flush()


class Broker:
    def __init__(self, slug: str, resume: bool):
        self.slug = slug
        self.p = paths(slug)
        self.film = PROJECTS_DIR / slug
        if not self.film.is_dir():
            raise Unavailable("missing-film")
        title = slug
        with contextlib.suppress(OSError, ValueError, AttributeError):
            t = json.loads((self.film / "project.json").read_text()).get("title")
            if isinstance(t, str) and t.strip():
                title = t
        self.title = title
        self.work = cs.work_dir(self.film)
        self.store = RequestStore(claude_dir(), slug)
        self.journal = Journal(self.p["events"])
        self.resume = False
        self.session_id = str(uuid.uuid4())
        if resume and self.p["session"].exists():   # the identity file is never a resume source
            with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                sid = json.loads(self.p["session"].read_text())["session_id"]
                if isinstance(sid, str) and sid:
                    self.session_id, self.resume = sid, True
        self.token = secrets.token_hex(32)
        self.addon_epoch: str | None = None
        self.last_seq = 0
        self.rows: collections.deque = collections.deque(maxlen=50)
        self.last_hello: dict | None = None
        self.state = "starting"
        self.turn_id: str | None = None
        self.clients: dict[asyncio.StreamWriter, dict] = {}
        self.lease: dict = {"page": None, "until": None}
        self.outbox: collections.deque = collections.deque()
        self.outbox_ready = asyncio.Event()
        self.unacked: dict[str, tuple[dict, float]] = {}
        self.notice_actions: dict[str, tuple[str, str]] = {}
        self.queued_notices: set[tuple[str, str]] = set()
        self.stop_pending: dict[str, float] = {}
        self.stop_noticed: set[str] = set()
        self.stop_floor = 0
        self.spawned_at: float | None = None
        self.last_heard = 0.0
        self.missing_sent = self.silent_sent = False
        # process and server handles (any may be None when shutdown runs)
        self.lock_fd: int | None = None
        self.master: int | None = None
        self.gate_w: int | None = None
        self.claude: subprocess.Popen | None = None
        self.claude_path = self.claude_version = None
        self.live_server: asyncio.AbstractServer | None = None
        self.client_server: asyncio.AbstractServer | None = None
        self.replay = bytearray()
        self.pty_pending = bytearray()
        self.shutting = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.tasks: list[asyncio.Task] = []
        self.selfcheck_proc: subprocess.Popen | None = None

    # -- lifecycle -----------------------------------------------------------------------------------
    def _check(self) -> None:
        if self.shutting:
            raise _Aborted()

    async def run(self) -> int:
        loop = self.loop = asyncio.get_running_loop()
        # 1. lifetime lock (a server may hold it for a moment while it checks)
        self.lock_fd = os.open(self.p["lock"], os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + 3
        while True:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self.lock_fd); self.lock_fd = None
                    return 0                                    # another broker is alive
                await asyncio.sleep(0.1)
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.ensure_future(self.shutdown("terminated")))
        # Ignore SIGHUP with a no-op handler, not SIG_IGN: an ignored disposition is inherited through exec,
        # so Claude (and its group) would then ignore the SIGHUP that shutdown sends it first.
        loop.add_signal_handler(signal.SIGHUP, lambda: None)
        try:
            claude, env = await self._prepare(loop)
        except _Aborted:
            await asyncio.Future()                              # the shutdown under way ends the process
        except Unavailable:
            raise
        except Exception as exc:
            _log(f"start failed before Claude: {exc!r}")
            self._shutting_without_claude()
            raise
        fail_at = os.environ.get("FRONTLOT_TEST_FAIL_AT")
        try:
            self._check()
            # 7. the live endpoint first, so Claude's first /hello has somewhere to go
            self.live_server = await asyncio.start_unix_server(self.handle_live, path=str(self.p["live"]))
            os.chmod(self.p["live"], 0o600)
            self._check()
            # 8. gated spawn, identity, open the gate, session file
            self.spawn_gated(claude, env)
            self.write_identity()
            if fail_at == "before-gate":
                raise RuntimeError("test failure before the gate")
            os.write(self.gate_w, b"go\n"); os.close(self.gate_w); self.gate_w = None
            self.spawned_at = time.time()
            if fail_at == "after-gate":
                raise RuntimeError("test failure after the gate")
            write_private(self.p["session"], json.dumps({"session_id": self.session_id}))
            # 9. publish: the client socket is bound last
            self._check()
            self.client_server = await asyncio.start_unix_server(self.handle_client, path=str(self.p["sock"]))
            os.chmod(self.p["sock"], 0o600)
            self._check()
            self.tasks.append(asyncio.ensure_future(self.watcher()))
            loop.add_reader(self.master, self._on_pty)
        except _Aborted:
            await asyncio.Future()                              # the shutdown under way ends the process
        except BaseException as exc:
            _log(f"start failed: {exc!r}")
            await self.shutdown("start-failed", code=1)
            raise
        await asyncio.Future()                                  # runs until shutdown() exits the process
        return 0

    async def _prepare(self, loop) -> tuple[str, dict]:
        """Steps 2-5. The slow calls (Claude's version and sign-in, the login PATH, the self-check) run in an
        executor so a SIGTERM is acted on at once; after each one a shutdown already under way wins."""
        # 2. leftovers no live broker owns (we hold the lock)
        self.p["unavailable"].unlink(missing_ok=True)
        self.p["sock"].unlink(missing_ok=True); self.p["live"].unlink(missing_ok=True)
        # 3. a predecessor that died without its own shutdown
        self.cancel_and_journal("previous-session-ended")
        # 4. Claude, settings, brief, env
        claude, version = await loop.run_in_executor(None, resolve_claude)
        self._check()
        self.claude_path, self.claude_version = claude, version
        write_private(self.p["settings"], json.dumps(cs.build_settings(
            repo_root=REPO, film_root=self.film, meta_root=metadata_root(), environ=os.environ), indent=2))
        write_private(self.p["brief"], cs.build_brief(film_title=self.title, film_slug=self.slug,
                                                       writeros_package=cs.writeros_package(self.film),
                                                       story_drive=cs.story_drive_folder(self.film)))
        login_path = (await loop.run_in_executor(None, _login_environment))["PATH"]
        self._check()
        env = cs.allowed_env(os.environ, login_path=login_path, live_socket=str(self.p["live"]), live_token=self.token)
        # 5. fail-closed sandbox self-check
        if not os.environ.get("FRONTLOT_SKIP_PREFLIGHT"):
            def remember(proc):                                 # runs in the executor thread
                self.selfcheck_proc = proc
                if self.shutting:                               # shutdown may have looked before this was set
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.killpg(proc.pid, signal.SIGKILL)
            passed = await loop.run_in_executor(None, lambda: sandbox_selfcheck(
                self.slug, claude, version, self.p["settings"], self.work, env, on_spawn=remember))
            self._check()                                       # a killed check reads as failed: never report it
            if not passed:
                raise Unavailable("sandbox", version)
        return claude, env

    def _shutting_without_claude(self) -> None:
        """A startup failure before anything ran: tell the server plainly, keep exit 1 (main)."""
        with contextlib.suppress(Exception):
            write_private(self.p["unavailable"], json.dumps({"reason": "start-failed"}))

    def _end_selfcheck(self) -> None:
        proc = self.selfcheck_proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            end = time.monotonic() + 5
            while proc.poll() is None and time.monotonic() < end:
                time.sleep(0.02)
        for f in canary_paths(self.slug, self.work):
            with contextlib.suppress(OSError):
                f.unlink(missing_ok=True)

    def spawn_gated(self, claude: str, env: dict) -> None:
        """Recoverable at every instant: the child waits on a pipe and runs Claude only after `go`."""
        self.master, slave = pty.openpty()
        os.set_blocking(self.master, False)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", *INITIAL_SIZE, 0, 0))
        r, self.gate_w = os.pipe()
        prompt = ("Picking back up. Give Ben the short check-in now." if self.resume
                  else "Give Ben the short check-in now.")
        argv = cs.launch_argv(claude=claude, mod_dir=REPO / "backlot" / "claude_mod", settings_file=self.p["settings"],
                              brief_file=self.p["brief"], session_id=self.session_id, resume=self.resume, prompt=prompt)
        try:
            self._check()
            self.claude = subprocess.Popen(
                ["/bin/sh", "-c", f'IFS= read -r go <&{r} && [ "$go" = go ] && exec "$@"; exit 70',
                 "frontlot-claude", *argv],
                cwd=self.work, env=env, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True, pass_fds=(r,))
        finally:
            os.close(slave); os.close(r)

    def write_identity(self) -> None:
        pid = self.claude.pid
        write_private(self.p["ident"], json.dumps({
            "session_id": self.session_id, "broker_pid": os.getpid(), "broker_started": proc_started(os.getpid()),
            "claude_pid": pid, "claude_pgid": pid, "claude_started": proc_started(pid), "boot_time": boot_time(),
            "claude_path": self.claude_path, "claude_version": self.claude_version}))

    def _group_gone(self) -> bool:
        if self.claude.poll() is None:
            return False
        try:
            os.killpg(self.claude.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    async def _end_claude(self) -> bool:
        """SIGHUP, then SIGTERM, then SIGKILL to Claude's whole group; True once the group is confirmed gone."""
        if self.gate_w is not None:                     # a child still at the gate exits without running Claude
            with contextlib.suppress(OSError):
                os.close(self.gate_w)
            self.gate_w = None
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL, None):
            if self._group_gone():
                return True
            if sig is not None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(self.claude.pid, sig)
            end = time.monotonic() + 5
            while time.monotonic() < end:
                if self._group_gone():
                    return True
                await asyncio.sleep(0.05)
        return self._group_gone()

    async def shutdown(self, reason: str, page: str | None = None, code: int = 0) -> None:
        """Idempotent; safe at any point of run(). Never returns: ends the process."""
        if self.shutting:
            await asyncio.Future()
        self.shutting = True
        with contextlib.suppress(Exception):
            self.cancel_and_journal(reason)
        with contextlib.suppress(Exception):
            self.state = "ended"
            self.journal_event({"kind": "session-state", "state": "ended", "reason": reason})
            self.send_status_all()
        if self.master is not None and self.loop is not None:
            with contextlib.suppress(Exception):
                self.loop.remove_reader(self.master)
                self.loop.remove_writer(self.master)
        with contextlib.suppress(Exception):
            self._end_selfcheck()                       # the check's group and its canaries, if one ran
        if reason == "start-failed":
            self._shutting_without_claude()
        if self.gate_w is not None:                     # spawn may have failed after the pipe was made
            with contextlib.suppress(OSError):
                os.close(self.gate_w)
            self.gate_w = None
        group_gone = True
        if self.claude is not None:
            try:
                group_gone = await self._end_claude()
            except Exception as exc:
                _log(f"ending Claude failed: {exc!r}"); group_gone = False
        bye = {"reason": reason, **({"page": page} if page else {})}
        for w in list(self.clients):
            with contextlib.suppress(Exception):
                w.write(cf.encode_json(cf.BYE, bye))
                await asyncio.wait_for(w.drain(), 1)
            with contextlib.suppress(Exception):
                w.close()
        for server in (self.client_server, self.live_server):
            if server is not None:
                with contextlib.suppress(Exception):
                    server.close()
        for key in ("sock", "live") + (("ident",) if group_gone and self.claude is not None else ()):
            with contextlib.suppress(OSError):
                self.p[key].unlink(missing_ok=True)
        if not group_gone:
            _log("Claude's process group is still alive; identity kept for the server to finish the job")
        if self.lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN); os.close(self.lock_fd)
        with contextlib.suppress(Exception):
            sys.stdout.flush(); sys.stderr.flush()
        os._exit(code)

    # -- helpers -------------------------------------------------------------------------------------
    def authorized(self, writer) -> bool:
        info = self.clients.get(writer)
        return info is not None and info["page"] == self.lease["page"]

    def _page_connected(self, page) -> bool:
        return any(c["page"] == page for c in self.clients.values())

    def _send(self, writer, frame: bytes) -> None:
        if writer.is_closing():
            return
        if writer.transport.get_write_buffer_size() > MAX_CLIENT_BUFFER:
            writer.close()                              # hopelessly behind: it reconnects and gets a snapshot
            return
        writer.write(frame)

    def _status(self, writer) -> bytes:
        return cf.encode_json(cf.STATUS, {"controller": self.authorized(writer), "state": self.state,
                                          "session_id": self.session_id})

    def send_status_all(self) -> None:
        for w in list(self.clients):
            self._send(w, self._status(w))

    @staticmethod
    def _event_frame(seq, ev: dict) -> bytes:
        try:
            return cf.encode_json(cf.EVENT, {"seq": seq, "event": ev})
        except cf.FrameError:
            return cf.encode_json(cf.EVENT, {"seq": seq, "event": {"kind": "notice", "plain": TOO_LONG}})

    def journal_event(self, ev: dict) -> int:
        seq = self.journal.append(ev)
        frame = self._event_frame(seq, ev)
        for w in list(self.clients):
            self._send(w, frame)
        return seq

    def notice(self, writer, plain: str) -> None:
        self._send(writer, cf.encode_json(cf.EVENT, {"seq": None, "event": {"kind": "notice", "plain": plain}}))

    def set_state(self, s: str) -> None:
        if s != self.state and self.state != "ended":
            self.state = s
            self.journal_event({"kind": "session-state", "state": s})
            self.send_status_all()

    def cancel_and_journal(self, reason: str) -> None:
        for rid in self.store.cancel_unstarted(reason=reason):
            self.journal_event({"kind": "spend-decided", "requestId": rid, "state": "cancelled", "reason": reason})
            self.store.mark_notified(rid, "cancelled")

    def launch_and_journal(self, rec: dict) -> None:
        rid = rec["id"]
        try:
            self.store.launch(rid, repo=REPO, env=run_env())
        except Rejected as e:
            self.journal_event({"kind": "notice", "plain": e.plain})
            return
        except Exception as exc:                        # never let a failed launch drop Ben's connection
            _log(f"launch of {rid} failed: {exc!r}")
            try:
                if self.store.cancel_if_unclaimed(rid, reason="launch-failed"):   # certainly never started
                    self.journal_event({"kind": "spend-decided", "requestId": rid, "state": "cancelled",
                                        "reason": "launch-failed"})
                    self.store.mark_notified(rid, "cancelled")
                    self.journal_event({"kind": "notice", "plain": LAUNCH_BROKE})
                else:                                   # claimed: reconcile settles it (uncertain), Claude is told
                    self.journal_event({"kind": "notice", "plain": LAUNCH_UNSURE})
            except Exception as exc2:
                _log(f"settling the failed launch of {rid} failed: {exc2!r}")
                self._notice_all(LAUNCH_UNSURE)
            return
        try:                                            # it started: never say otherwise from here on
            self.journal_event({"kind": "run-started", **card(self.store.get(rid))})
        except Exception as exc:
            _log(f"run-started for {rid} could not be journaled: {exc!r}")
            self._notice_all(STARTED_UNSHOWN)

    def _notice_all(self, plain: str) -> None:
        for w in list(self.clients):
            with contextlib.suppress(Exception):
                self.notice(w, plain)

    def enqueue(self, action: dict, front: bool = False) -> None:
        """The only way anything enters the outbox; always wakes a waiting poll."""
        if front:
            self.outbox.appendleft(action)
        else:
            self.outbox.append(action)
        self.outbox_ready.set()

    def tell_claude(self, text: str) -> str:
        aid = uuid.uuid4().hex
        self.enqueue({"id": aid, "submit": text})
        return aid

    def snapshot(self) -> dict:
        return {"state": self.state, "hello": self.last_hello, "rows": list(self.rows),
                "cards": [card(r) for r in self.store.all_requests() if r["session"] == self.session_id]}

    def _heard(self) -> None:
        self.last_heard = time.time()
        if self.silent_sent:
            self.silent_sent = False
            self.journal_event({"kind": "addon-back"})

    def _is_stale_stop(self, action: dict) -> bool:
        return "stop" in action and action.get("origin_epoch") != self.addon_epoch

    # -- live endpoint (the add-on) ------------------------------------------------------------------
    async def handle_live(self, reader, writer) -> None:
        try:
            try:
                route, headers, body = await read_http(reader)
            except Exception:
                return
            if not secrets.compare_digest(headers.get("x-frontlot-token", "").encode(), self.token.encode()):
                http_reply(writer, "401 Unauthorized", {})
            else:
                try:
                    reply = await self.route_live(route, body)
                except Exception as exc:
                    _log(f"{route} failed: {exc!r}")
                    reply = ("500 Internal Server Error", {})
                if reply is None:
                    http_reply(writer, "404 Not Found", {})
                elif isinstance(reply, tuple):
                    http_reply(writer, *reply)
                else:
                    http_reply(writer, "200 OK", reply)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.drain(), 10)
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def route_live(self, route: str, body: dict):
        if route == "/hello":
            return self.on_hello(body)
        if route == "/report":
            return self.on_report(body)
        if route == "/ping":
            self._heard()
            return {}
        if route == "/inbox":
            return await self.on_inbox(body)
        if route == "/inbox-ack":
            return self.on_inbox_ack(body)
        if route == "/run":
            return self.on_run(body)
        if route == "/run-check":
            key = body.get("key")
            rec = self.store.by_key(key) if isinstance(key, str) and KEY_RE.match(key) else None
            return reply_for(rec) if rec else {"status": "not-received", "plain": "Front Lot has no request with that key."}
        return None

    def on_hello(self, body: dict):
        epoch = body.get("epoch")
        if not isinstance(epoch, str) or not epoch:
            return ("400 Bad Request", {})
        if self.addon_epoch is not None:                # a reload, /clear, or resume inside Claude
            self.cancel_and_journal("addon-reload")
        # A new epoch retires every old turn, so every Stop issued before it is settled (also a Stop sent
        # before the very first hello, whose origin epoch is None). Messages to Claude stay queued.
        self.stop_pending.clear(); self.stop_noticed.clear()
        self.stop_floor = 0                              # the add-on's turn counter starts over
        self.outbox = collections.deque(a for a in self.outbox if "stop" not in a)
        self.unacked = {k: v for k, v in self.unacked.items() if "stop" not in v[0]}
        self.addon_epoch = epoch
        self.last_seq = 0
        self.last_hello = body
        self.turn_id = None
        self._heard()
        self.journal_event({"kind": "addon-hello", "hello": body})
        self.set_state("ready")
        self.outbox_ready.set()                          # wakes polls of the old epoch so they answer {}
        return {}

    def on_report(self, body: dict):
        if body.get("epoch") != self.addon_epoch or self.addon_epoch is None:
            return {"acceptedThrough": 0}
        events = body.get("events")
        for ev in events if isinstance(events, list) else []:
            if not isinstance(ev, dict) or ev.get("seq") != self.last_seq + 1 or isinstance(ev.get("seq"), bool):
                continue
            self.last_seq = ev["seq"]
            rec = {**ev, "epoch": self.addon_epoch}
            self.journal_event(rec)
            kind = ev.get("kind")
            if kind == "row":
                self.rows.append(rec)
            elif kind == "turn" and not ev.get("agentId"):
                if ev.get("phase") == "start":
                    self.turn_id = ev.get("turnId")
                    self.set_state("working")
                elif ev.get("phase") == "complete" and ev.get("turnId") == self.turn_id:
                    self.turn_id = None
                    self.set_state("ready")
        self._heard()
        return {"acceptedThrough": self.last_seq}

    def _take(self) -> dict | None:
        now = time.time()
        for aid, (action, sent) in list(self.unacked.items()):
            if now - sent >= REDELIVER_SECONDS:
                if self._is_stale_stop(action):
                    del self.unacked[aid]
                    continue
                return action
        while self.outbox:
            action = self.outbox.popleft()
            if not self._is_stale_stop(action):         # a Stop from an ended epoch is never delivered
                return action
        return None

    async def on_inbox(self, body: dict) -> dict:
        poll_epoch = body.get("epoch")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + POLL_SECONDS
        while True:
            # Checked after every wait and with no await before the take: a poll from an epoch that ended
            # while it waited never takes (and so never eats) an action.
            if self.shutting or poll_epoch is None or poll_epoch != self.addon_epoch:
                return {}
            action = self._take()
            if action is not None:
                self.unacked[action["id"]] = (action, time.time())
                out = {k: v for k, v in action.items() if k != "origin_epoch"}
                out["epoch"] = self.addon_epoch
                return out
            remaining = deadline - loop.time()
            if remaining <= 0:
                return {}
            self.outbox_ready.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.outbox_ready.wait(), min(remaining, max(0.2, REDELIVER_SECONDS / 2)))

    def on_inbox_ack(self, body: dict) -> dict:
        aid, status, reason = body.get("id"), body.get("status"), body.get("reason") or ""
        if not isinstance(aid, str) or status not in ("queued", "submitted", "rejected"):
            return {}
        if status == "queued":                           # not final: stays unacked (a redelivery is acked queued again)
            return {}
        entry = self.unacked.pop(aid, None)
        if status == "rejected" and reason == "epoch ended":
            if entry is not None and "submit" in entry[0]:
                self.enqueue(entry[0], front=True)       # it belongs to the conversation, not to the dead epoch
            return {}
        if aid in self.notice_actions:
            rid, st = self.notice_actions.pop(aid)
            self.store.mark_notified(rid, st)
            if status == "rejected":
                rec = self.store.get(rid) or {}
                self.journal_event({"kind": "notice", "plain": f"Claude couldn't be told that "
                                    f"{rec.get('summary', 'a run')} {NOTICE_WORD.get(st, st)}."})
        if aid in self.stop_pending:
            m = STOPPED_THROUGH.match(reason)
            if m:                                        # any final status: the floor is what matters
                self.stop_floor = max(self.stop_floor, int(m.group(1)))
                del self.stop_pending[aid]
                self.stop_noticed.discard(aid)
        return {}

    def on_run(self, body: dict) -> dict:
        key = body.get("key")
        if not isinstance(key, str) or not KEY_RE.match(key):
            return {"status": "refused", "plain": "missing request key"}
        existing = self.store.by_key(key)
        if existing:
            return reply_for(existing)                   # never prepared again
        seq = body.get("turnSeq")
        if (self.shutting or self.addon_epoch is None or body.get("epoch") != self.addon_epoch or self.stop_pending
                or (self.stop_floor > 0 and not (isinstance(seq, int) and not isinstance(seq, bool)
                                                 and seq > self.stop_floor))):
            return {"status": "refused", "plain": STALE}
        try:
            prep = prepare(body.get("op"), body.get("params") or {}, repo=REPO, film_slug=self.slug,
                           film_root=self.film, snapshot_dir=claude_dir() / self.slug / "snap" / key)
        except OpError as e:
            return {"status": "refused", "plain": str(e)}
        rec = self.store.create(prep, key=key, session=self.session_id, epoch=self.addon_epoch, film_root=self.film)
        if not rec["paid"]:
            if rec["state"] == "approved":
                self.launch_and_journal(rec)
            return reply_for(self.store.get(rec["id"]))
        self.journal_event({"kind": "spend-request", **card(rec)})
        return reply_for(rec)

    # -- client socket (Front Lot's server) ----------------------------------------------------------
    async def handle_client(self, reader, writer) -> None:
        try:
            t, payload = await asyncio.wait_for(cf.read_frame(reader), 10)
            hello = cf.decode_json(t, payload) if t == cf.HELLO else None
        except Exception:
            hello = None
        page = hello.get("page") if hello else None
        if not isinstance(page, str) or not page or self.shutting:
            with contextlib.suppress(Exception):
                writer.close()
            return
        self.clients[writer] = {"page": page}
        try:
            holder = self.lease["page"]
            if hello.get("controller") is True and (
                    holder in (None, page)
                    or (not self._page_connected(holder) and self.lease["until"] is not None
                        and time.time() >= self.lease["until"])):
                self.lease = {"page": page, "until": None}
                self.send_status_all()
            else:
                self._send(writer, self._status(writer))
            tail = bytes(_safe_replay_tail(self.replay, REPLAY_BYTES))
            for i in range(0, len(tail), cf.LIMITS[cf.OUT]):
                self._send(writer, cf.encode(cf.OUT, tail[i:i + cf.LIMITS[cf.OUT]]))
            start = hello.get("subscribe_from")
            start = start if isinstance(start, int) and not isinstance(start, bool) else 0
            events, snap = self.journal.since(start, self.snapshot)
            if snap is not None:
                self._send(writer, self._snapshot_frame(snap))
            for e in events:
                self._send(writer, self._event_frame(e["seq"], e["event"]))
            while True:
                t, payload = await cf.read_frame(reader)
                if self.shutting:
                    continue
                if t == cf.IN:
                    if self.authorized(writer):
                        self._write_pty(payload)
                elif t == cf.RESIZE:
                    if self.authorized(writer):
                        size = valid_resize(cf.decode_json(t, payload))
                        if size is not None and self.master is not None:
                            cols, rows = size
                            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                elif t == cf.ACTION:
                    await self.on_action(writer, page, cf.decode_json(t, payload))
        except (asyncio.IncompleteReadError, cf.FrameError, ConnectionError):
            pass
        except Exception as exc:                        # never take the broker down over one connection
            _log(f"client connection failed: {exc!r}")
        finally:
            self.clients.pop(writer, None)
            if self.lease["page"] == page and not self._page_connected(page):
                self.lease["until"] = time.time() + LEASE_SECONDS
            with contextlib.suppress(Exception):
                writer.close()

    @staticmethod
    def _snapshot_frame(snap: dict) -> bytes:
        """Fit a snapshot in one frame, giving things up in this order: the hello (its history can be huge), then
        the oldest rows, and the spend cards only last, since a hidden card is a decision Ben can't see."""
        snap = dict(snap)
        while True:
            try:
                return cf.encode_json(cf.EVENT, {"seq": None, "event": snap})
            except cf.FrameError:
                if snap.get("hello") is not None:
                    snap["hello"] = None
                elif snap.get("rows"):
                    snap["rows"] = snap["rows"][len(snap["rows"]) // 2 + 1:]   # keep the newest rows that fit
                elif snap.get("cards"):
                    snap["cards"] = snap["cards"][len(snap["cards"]) // 2 + 1:]
                else:
                    return cf.encode_json(cf.EVENT, {"seq": None, "event": {"kind": "notice", "plain": TOO_LONG}})

    async def on_action(self, writer, page: str, action: dict) -> None:
        kind = action.get("type")
        if kind in ("submit", "stop", "spend-decision", "end", "new") and not self.authorized(writer):
            self.notice(writer, READ_ONLY)
            return
        if kind == "submit":
            text = action.get("text")
            if isinstance(text, str) and text.strip():
                self.tell_claude(text)
        elif kind == "stop":
            sid = uuid.uuid4().hex
            self.stop_pending[sid] = time.time()
            self.enqueue({"id": sid, "stop": {"turnId": "*"}, "origin_epoch": self.addon_epoch}, front=True)
            self.cancel_and_journal("stop")
        elif kind == "spend-decision":
            rid = action.get("requestId")
            if not isinstance(rid, str) or not RID_RE.match(rid):
                self.notice(writer, "That request is gone.")
                return
            try:
                rec = self.store.decide(rid, go=bool(action.get("go")), session=self.session_id,
                                        epoch=self.addon_epoch or "", controller=True)
            except Rejected as e:
                self.notice(writer, e.plain)
                cur = self.store.get(rid)
                if cur and cur["state"] in ("expired", "cancelled"):
                    self.journal_event({"kind": "spend-decided", "requestId": rid, "state": cur["state"],
                                        **({"reason": cur["note"]} if cur.get("note") else {})})
                    if cur["state"] == "cancelled":
                        self.store.mark_notified(rid, "cancelled")   # journaled here; the watcher need not repeat it
                return
            except Exception as exc:                    # never let a failed write drop Ben's connection
                _log(f"decision on {rid} failed: {exc!r}")
                self.notice(writer, DECIDE_BROKE)
                return
            self.journal_event({"kind": "spend-decided", "requestId": rid, "state": rec["state"]})
            if rec["state"] == "approved":
                self.launch_and_journal(rec)
            else:
                self.tell_claude(f"[Front Lot] Ben said Not now to: {rec['summary']}.")
        elif kind == "take-control":
            old = self.lease["page"]
            self.lease = {"page": page, "until": None}
            self.send_status_all()
            if old not in (None, page):
                for w, info in list(self.clients.items()):
                    if info["page"] == old:
                        self.notice(w, "Another window took control.")
                        break
        elif kind == "end":
            await self.shutdown("ended")
        elif kind == "new":
            await self.shutdown("new", page=page)

    # -- PTY -----------------------------------------------------------------------------------------
    def _on_pty(self) -> None:
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            with contextlib.suppress(Exception):
                self.loop.remove_reader(self.master)
            asyncio.ensure_future(self.shutdown("claude-exited"))
            return
        self.replay += data
        if len(self.replay) > 2 * REPLAY_BYTES:
            self.replay = bytearray(_safe_replay_tail(self.replay, REPLAY_BYTES))
        frame = cf.encode(cf.OUT, data)
        for w in list(self.clients):
            self._send(w, frame)

    def _write_pty(self, data: bytes) -> None:
        if self.master is None:
            return
        if len(self.pty_pending) + len(data) > MAX_PTY_PENDING:
            return                                      # Claude isn't reading its input; drop, never block the loop
        self.pty_pending += data
        self._flush_pty()

    def _flush_pty(self) -> None:
        try:
            while self.pty_pending:
                n = os.write(self.master, self.pty_pending)
                del self.pty_pending[:n]
        except BlockingIOError:
            pass
        except OSError:
            self.pty_pending.clear()
        if self.pty_pending:
            self.loop.add_writer(self.master, self._flush_pty)
        else:
            with contextlib.suppress(Exception):
                self.loop.remove_writer(self.master)

    # -- watcher -------------------------------------------------------------------------------------
    async def watcher(self) -> None:
        while not self.shutting:
            try:
                self.watch_once()
            except Exception as exc:
                _log(f"watcher pass failed: {exc!r}")
            if self.claude is not None and self.claude.poll() is not None:
                await self.shutdown("claude-exited")
            await asyncio.sleep(WATCH_SECONDS)

    def _outcome_message(self, rec: dict) -> str:
        st = rec["state"]
        if st == "failed" and rec.get("note"):
            tail = rec["note"]
        elif st == "uncertain":
            tail = ""
        else:
            tail = str((rec.get("result") or {}).get("tail") or "").strip()
            if len(tail) > 600:
                tail = tail[-600:]
                tail = tail.split("\n", 1)[1] if "\n" in tail else tail
        return f"[Front Lot] {rec['summary']}: {OUTCOME_WORD[st]}. {tail}".rstrip()

    def watch_once(self) -> None:
        now = time.time()
        # (a) settle runs, tell Claude (each pending notice once per broker life; again after a restart until acked)
        self.store.reconcile()
        for rec in self.store.pending_notices():
            pair = (rec["id"], rec["state"])
            if pair in self.queued_notices:
                continue
            st = rec["state"]
            if st in ("done", "failed", "uncertain"):
                self.journal_event({"kind": "run-finished", **card(rec)})
                aid = self.tell_claude(self._outcome_message(rec))
            elif st == "expired":
                self.journal_event({"kind": "spend-decided", "requestId": rec["id"], "state": "expired"})
                aid = self.tell_claude(f"[Front Lot] The card for {rec['summary']} expired unanswered.")
            elif st == "cancelled":
                self.journal_event({"kind": "spend-decided", "requestId": rec["id"], "state": "cancelled",
                                    "reason": rec.get("note")})
                self.store.mark_notified(rec["id"], "cancelled")
                continue
            else:
                continue
            self.notice_actions[aid] = pair
            self.queued_notices.add(pair)
        for sid, sent in list(self.stop_pending.items()):
            if now - sent > STOP_NOTICE_SECONDS and sid not in self.stop_noticed:
                self.stop_noticed.add(sid)
                self.journal_event({"kind": "notice", "plain": NO_STOP_CONFIRM})
        # (b) cards nobody answered (they reach Claude through (a) on the next pass)
        self.store.expire_stale()
        # (c) add-on health
        if self.last_hello is None:
            if (self.spawned_at is not None and now - self.spawned_at > NO_HELLO_SECONDS
                    and not self.missing_sent):
                self.missing_sent = True
                self.journal_event({"kind": "addon-missing"})
        elif now - self.last_heard > SILENT_SECONDS and not self.silent_sent:
            self.silent_sent = True
            self.journal_event({"kind": "addon-silent"})
        # (d) lease expiry
        until = self.lease["until"]
        if until is not None and now >= until and not self._page_connected(self.lease["page"]):
            self.lease = {"page": None, "until": None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-film Claude session broker for Front Lot.")
    ap.add_argument("--broker", action="store_true", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args(argv)
    if not SLUG_RE.match(a.project) or ".." in a.project:
        print("not a film id", file=sys.stderr)
        return 2
    broker = None
    try:
        broker = Broker(a.project, a.resume)
        return asyncio.run(broker.run())
    except Unavailable as u:
        p = paths(a.project)
        write_private(p["unavailable"], json.dumps({"reason": u.reason, **({"version": u.version} if u.version else {})}))
        if broker is not None and broker.lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(broker.lock_fd, fcntl.LOCK_UN); os.close(broker.lock_fd)
        return 3
    except Exception:
        if broker is not None and broker.lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(broker.lock_fd, fcntl.LOCK_UN); os.close(broker.lock_fd)
        raise                                            # exit 1; the start-failed file is already written


if __name__ == "__main__":
    sys.exit(main())
