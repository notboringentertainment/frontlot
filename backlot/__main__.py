"""Backlot CLI.

    python -m backlot open [project-id]   # start server if needed, open browser
    python -m backlot serve [--port N]    # run the server in the foreground

``open`` is idempotent and non-fatal by design: agents call it at pipeline
initialization and must continue the production even if it fails.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

from backlot import DEFAULT_PORT


def _port() -> int:
    try:
        return int(os.environ.get("BACKLOT_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def _server_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _reserve_listener(port: int) -> socket.socket:
    """Bind Backlot's loopback port before minting a token for it.

    Holding this socket through Uvicorn startup closes the bind/token race:
    a second ``serve`` cannot replace a live server's capability token, and an
    unrelated occupied port fails before this process writes any token file.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                listener.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
        return listener
    except Exception:
        listener.close()
        raise


def _spawn_server(port: int) -> None:
    """Start the server as a detached background process."""
    from backlot.server import server_log_path

    cmd = [sys.executable, "-m", "backlot", "serve", "--port", str(port)]
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
    else:
        kwargs["start_new_session"] = True
    # Keep detached startup, but retain all lifecycle diagnostics in the
    # private Backlot log instead of discarding stderr.
    with server_log_path().open("a", encoding="utf-8") as log_file:
        kwargs["stdout"] = log_file
        kwargs["stderr"] = log_file
        subprocess.Popen(cmd, **kwargs)


def cmd_open(project_id: str | None) -> int:
    port = _port()
    if not _server_alive(port):
        try:
            _spawn_server(port)
        except Exception as exc:
            print(f"backlot: could not start server ({exc}) — continuing without the board")
            return 1
        deadline = time.time() + 15
        while time.time() < deadline:
            if _server_alive(port):
                break
            time.sleep(0.4)
        else:
            print("backlot: server did not come up in time — continuing without the board")
            return 1
    from backlot.server import read_capability_token

    token = read_capability_token(port)
    if token is None:
        print("backlot: server did not publish its capability token — continuing without the board")
        return 1
    display_url = f"http://127.0.0.1:{port}/"
    url = f"{display_url}#k={token}"
    if project_id:
        display_url = f"http://127.0.0.1:{port}/p/{project_id}"
        url = f"{display_url}#k={token}"
    try:
        webbrowser.open(url)
    except Exception:
        pass
    # Do not echo the fragment: shell history/transcripts are not a safe
    # capability-token store. The browser still receives the full URL above.
    print(f"backlot: {display_url}")
    return 0


def cmd_serve(port: int) -> int:
    import uvicorn
    from backlot.server import create_app, server_log, write_capability_token

    # Backlot keeps its capability token and future PTY broker in-process;
    # multiple workers would split that state and invalidate the handshake.
    workers = 1
    assert workers == 1, "Backlot must run with exactly one uvicorn worker"
    try:
        listener = _reserve_listener(port)
    except OSError as exc:
        if _server_alive(port):
            print(f"backlot: server already running on 127.0.0.1:{port}; keeping its capability token")
            server_log(f"server start skipped: Backlot already owns 127.0.0.1:{port}")
        else:
            print(f"backlot: could not reserve 127.0.0.1:{port} ({exc})")
            server_log(f"server start failed: port 127.0.0.1:{port} is unavailable")
        return 1
    try:
        token = write_capability_token(port)
    except Exception as exc:
        listener.close()
        print(f"backlot: could not publish capability token ({exc})")
        server_log(f"server start failed: could not publish token for 127.0.0.1:{port}")
        return 1
    app = create_app(port=port, capability_token=token)
    # timeout_graceful_shutdown: without it uvicorn waits FOREVER for open
    # SSE event streams (which never end by design), leaving a half-dead
    # zombie on shutdown — not accepting requests but still heartbeating old
    # connections, so every open board tab keeps trusting stale state
    # (verified live 2026-09-01). Force-closing the streams lets tabs see the
    # drop and self-heal.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, workers=workers, log_level="warning",
                            timeout_graceful_shutdown=3)
    assert config.workers == 1, "Backlot must run with exactly one uvicorn worker"
    server = uvicorn.Server(config)
    server_log(f"server starting on 127.0.0.1:{port}")
    try:
        # ``listener`` was reserved before token creation; handing the exact
        # socket to Uvicorn preserves exclusive port ownership at startup.
        server.run(sockets=[listener])
    finally:
        listener.close()
        server_log(f"server stopped on 127.0.0.1:{port}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backlot", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    p_open = sub.add_parser("open", help="open the board in the browser (starts server if needed)")
    p_open.add_argument("project_id", nargs="?", default=None)

    p_serve = sub.add_parser("serve", help="run the Backlot server in the foreground")
    p_serve.add_argument("--port", type=int, default=_port())

    args = parser.parse_args(argv)
    if args.command == "open":
        return cmd_open(args.project_id)
    if args.command == "serve":
        return cmd_serve(args.port)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
