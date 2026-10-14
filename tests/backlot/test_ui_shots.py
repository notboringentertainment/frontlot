"""Browser test: supervised shots in the trim bin and their takes in the viewer (fake claude, no spend)."""

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
DIRECTION = ("One five-second medium close-up of hero-a as a continuity test. "
             "Hold a steady gaze, then a slight head turn.")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _row(shot: Path, row: dict) -> None:
    with (shot / "history.jsonl").open("a") as out:
        out.write(json.dumps(row) + "\n")


def add_take(film: Path, take_id: str, digest: str, at: str) -> str:
    rel = f"production/shots/shot-a/takes/{digest}.mp4"
    (film / rel).parent.mkdir(parents=True, exist_ok=True)
    (film / rel).write_bytes(b"not really a video")
    _row(film / "production" / "shots" / "shot-a", {
        "event_id": f"ev-{take_id}", "at": at, "kind": "take", "take_id": take_id, "path": rel,
        "sha256": digest, "duration_seconds": 5.0, "cost_usd": 0.56, "brief_revision_id": "rev1",
        "user_note": "made"})
    return rel


@pytest.fixture(scope="module")
def shots_server():
    root = Path(tempfile.mkdtemp(prefix="om-ui-", dir="/tmp"))   # AF_UNIX path limit
    gates = root / "g"
    gates.mkdir()
    home = root / "home"
    home.mkdir()
    film = root / "projects" / "film"
    shot = film / "production" / "shots" / "shot-a"
    shot.mkdir(parents=True)
    (film / "project.json").write_text(json.dumps({"title": "Film"}))
    _row(shot, {"event_id": "rev1", "at": "2026-01-02T10:00:00+00:00", "kind": "brief", "user_note": "ok",
                "brief": {"shot_id": "shot-a", "direction": DIRECTION, "spend_allowance_usd": 15.0,
                          "max_video_takes": 2, "allowed_tools": ["kling_reference_video"]}})
    first = add_take(film, "t1", "a" * 64, "2026-01-02T11:00:00+00:00")
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "OPENMONTAGE_PROJECTS_DIR": str(root / "projects"),
        "OPENMONTAGE_GATES_DIR": str(gates),
        "FRONTLOT_CLAUDE": str(claude_fakes.write_fake_claude(root)),
        "FRONTLOT_SKIP_PREFLIGHT": "1",
        "FRONTLOT_LEASE_SECONDS": "0.2",
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
        yield f"http://127.0.0.1:{port}", token, film, first
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        claude_fakes.stop_brokers(gates)
        shutil.rmtree(root, ignore_errors=True)


def test_a_shot_opens_in_the_viewer_and_plays_its_take(shots_server):
    base, token, _film, first = shots_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            page.goto(f"{base}/p/film?static=1#k={token}", wait_until="load")
            clip = page.locator(".bin .clip", has_text="continuity test")
            clip.wait_for(timeout=10000)
            assert "shots" in page.locator(".bin .plate-label").inner_text().lower()
            clip.click()
            video = page.locator("#viewer .screen video")
            video.wait_for(timeout=5000)
            assert video.get_attribute("src") == f"/media/film/{first}"
            assert video.get_attribute("controls") is not None
            viewer_text = page.locator("#viewer").inner_text()
            assert "continuity test" in viewer_text
            assert "a" * 16 not in viewer_text and "shot-a" not in viewer_text
            assert "$0.56" in viewer_text
            # Yellow is reserved for "needs you"; nothing about a shot waits on Ben here.
            assert page.locator("#viewer .waiting, .bin .clip .needs-mark").count() == 0
        finally:
            browser.close()


def test_a_new_take_lands_on_the_board_without_a_reload(shots_server):
    base, token, film, first = shots_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            page.goto(f"{base}/p/film#k={token}", wait_until="load")
            page.locator(".bin .clip", has_text="continuity test").click(timeout=10000)
            page.locator("#viewer .screen video").wait_for(timeout=5000)
            assert page.locator("#viewer .strip .frame").count() == 0   # one take: no strip yet
            second = add_take(film, "t2", "b" * 64, "2026-01-02T12:00:00+00:00")
            page.wait_for_function("() => document.querySelectorAll('#viewer .strip .frame').length === 2",
                                   timeout=15000)
            # The take on screen stays put; Ben picks the new one from the strip.
            assert page.locator("#viewer .screen video").get_attribute("src") == f"/media/film/{first}"
            page.locator("#viewer .strip .frame").nth(1).click()
            page.wait_for_function(
                f"() => document.querySelector('#viewer .screen video').getAttribute('src') === '/media/film/{second}'",
                timeout=5000)
        finally:
            browser.close()
