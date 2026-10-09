"""Browser test for the conversation column (fake claude, no real model, no spend)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

from tests.backlot import claude_fakes

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def session_server():
    root = Path(tempfile.mkdtemp(prefix="om-ui-", dir="/tmp"))   # AF_UNIX path limit
    gates = root / "g"
    gates.mkdir()
    home = root / "home"
    home.mkdir()
    film = root / "projects" / "film"
    film.mkdir(parents=True)
    (film / "project.json").write_text(json.dumps({"title": "Film"}))
    port = _free_port()
    env = dict(os.environ)
    env.update(claude_fakes.stub_ops_env(root))     # test-only operations for the spend card test
    env.update({
        "HOME": str(home),
        "OPENMONTAGE_PROJECTS_DIR": str(root / "projects"),
        "OPENMONTAGE_GATES_DIR": str(gates),
        "FRONTLOT_CLAUDE": str(claude_fakes.write_fake_claude(root)),
        "FRONTLOT_SKIP_PREFLIGHT": "1",
        "FRONTLOT_LEASE_SECONDS": "0.2",    # a closed test window must not hold control against the next one
    })
    server = subprocess.Popen(
        [sys.executable, "-m", "backlot", "serve", "--port", str(port)],
        cwd=REPO_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1):
                break
        except Exception:
            time.sleep(0.2)
    else:
        server.terminate()
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError("Front Lot server did not become healthy")
    token = (home / ".openmontage" / "backlot" / f"{port}.token").read_text().strip()
    try:
        yield f"http://127.0.0.1:{port}", token, gates, film
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        claude_fakes.stop_brokers(gates)
        shutil.rmtree(root, ignore_errors=True)


def test_column_starts_claude_and_falls_back_to_the_terminal(session_server):
    base, token, _gates, _film = session_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            page.goto(f"{base}/p/film#k={token}", wait_until="load")
            # The fake has no add-on, so after the no-hello window the column shows the raw terminal.
            page.wait_for_function(
                "() => document.querySelector('#session-terminal') && !document.querySelector('#session-terminal').hidden"
                " && document.querySelector('#session-terminal').textContent.includes('FAKE CLAUDE READY')",
                timeout=15000,
            )
            page.wait_for_function(
                "() => { const n = document.getElementById('session-note'); return !n.hidden && n.textContent.trim().length > 0; }",
                timeout=15000,
            )
            assert page.locator("#history.log-history").count() == 1
            assert "needs you" in page.locator("#log-feed").inner_text().lower()
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            assert page.evaluate("document.documentElement.scrollWidth") <= 390
        finally:
            browser.close()


def test_opening_the_terminal_leaves_the_conversation_in_control(session_server):
    """The live and tty sockets share one page id, so the terminal never takes control from the conversation."""
    base, token, _gates, _film = session_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            pages_sent: dict[str, set[str]] = {"live": set(), "tty": set()}

            def watch(ws):
                kind = "tty" if ws.url.endswith("/claude/tty") else "live" if ws.url.endswith("/claude/live") else None
                if kind is None:
                    return

                def on_sent(payload):
                    try:
                        value = json.loads(payload) if isinstance(payload, str) else {}
                    except ValueError:
                        return
                    if isinstance(value, dict) and "page" in value:
                        pages_sent[kind].add(value["page"])

                ws.on("framesent", on_sent)

            page.on("websocket", watch)
            page.goto(f"{base}/p/film#k={token}", wait_until="load")
            page.wait_for_function(
                "() => document.querySelector('#session-terminal').textContent.includes('FAKE CLAUDE READY')", timeout=15000)
            # One page id for both sockets: the broker's controller lease is per page, so a second id would
            # take control away from the conversation (seen in the first real run).
            assert len(pages_sent["live"]) == 1 and pages_sent["live"] == pages_sent["tty"], pages_sent
            # The terminal socket is open; the composer must still be usable (controller lease intact).
            page.wait_for_timeout(1000)
            assert page.locator("#composer-text").is_enabled()
            assert page.locator("#session-state button", has_text="Take control").count() == 0
            # Keys typed into the terminal reach Claude (only the controlling page's keystrokes are accepted).
            page.locator("#session-terminal").click()
            page.keyboard.type("hello fake\n")
            page.wait_for_function(
                "() => document.querySelector('#session-terminal').textContent.includes('echo:hello fake')", timeout=10000)
            assert page.locator("#composer-text").is_enabled()
        finally:
            browser.close()


def test_live_view_shows_prose_and_a_spend_card_that_runs_on_go(session_server):
    base, token, _gates, film = session_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            page.goto(f"{base}/p/film#k={token}", wait_until="load")
            page.wait_for_function(
                "() => document.querySelector('#session-terminal').textContent.includes('FAKE CLAUDE READY')", timeout=15000)
            sock, live_token = claude_fakes.live_endpoint(film)
            claude_fakes.hello(sock, live_token, "e-ui")
            claude_fakes.post_live(sock, live_token, "/report", {"epoch": "e-ui", "events": [
                {"seq": 1, "kind": "row", "uuid": "u1", "door": "d", "type": "assistant", "role": "assistant",
                 "origin": {"kind": "model", "model": "x"}, "blocks": [{"type": "text", "text": "Two looks are **ready**."}]}]})
            page.wait_for_function("() => document.querySelector('#session-feed').textContent.includes('Two looks are ready.')", timeout=10000)
            assert page.locator("#session-terminal").is_hidden()
            assert page.locator("#session-feed strong").inner_text() == "ready"
            claude_fakes.post_live(sock, live_token, "/run", {"key": "k-ui", "op": "test_paid", "params": {}, "epoch": "e-ui", "turnId": ""})
            page.wait_for_selector(".spend-card.waiting-for-ben", timeout=10000)
            assert "up to $0.12" in page.locator(".spend-card").inner_text()
            assert "waiting for you" in page.locator("#session-state").inner_text().lower()
            page.get_by_role("button", name="Go").click()
            page.wait_for_function(
                "() => document.querySelector('#session-feed .run-line.done') !== null", timeout=20000)
            assert "hero-a" in page.locator(".run-line.done").inner_text()
        finally:
            browser.close()
